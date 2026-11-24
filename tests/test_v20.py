"""Prove della versione 27: un lavoro non va a un computer scollegato o spento mentre il launcher aspettava."""
import importlib.util
import json
import shutil
import socket
import tempfile
import threading
import time
import unittest
import urllib.parse
from pathlib import Path

from base import ROOT, Base

from server import db, local

_spec = importlib.util.spec_from_file_location("sfb_launcher_v20", str(ROOT / "launcher" / "alvea_launcher.py"))
L = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(L)


class LavoriSoloAChiCe(Base):
    def device(self, admin, name):
        code = self.call("POST", "/api/devices/code", {}, admin)[1]["code"]
        status, out = self.call("POST", "/api/launcher/pair", {"code": code, "name": name, "specs": {"os": "linux", "cores": 4},
                                                                 "checks": {"pkg:dev": {"ok": True}}, "launcher_version": "1.2.0"})
        self.assertEqual(status, 200, out)
        return out["token"], out["device_id"]

    def job(self, tenant_id):
        con = db.connect()
        cur = con.execute("INSERT INTO jobs(tenant_id,agent_id,tool,package,place,args,requested_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
                          (tenant_id, "dev", "dev_elenca_file", "dev", "locale", "{}", 1, db.now()))
        con.close()
        return cur.lastrowid

    def state(self, job_id):
        con = db.connect()
        row = con.execute("SELECT status, device_id FROM jobs WHERE id=?", (job_id,)).fetchone()
        con.close()
        return row["status"], row["device_id"]

    def test_un_computer_scollegato_mentre_aspettava_non_riceve_il_lavoro(self):
        admin, tenant_id = self.new_tenant("Attesa Scollegata", "completo")
        token, device_id = self.device(admin, "PC che verrà scollegato")
        got = {}
        waiting = threading.Thread(target=lambda: got.update(zip(("status", "out"), self.call("POST", "/api/launcher/jobs/claim", {"wait": 6}, token))))
        waiting.start()
        time.sleep(1)  # il launcher è in attesa di lavoro quando l'amministratore lo scollega
        self.assertEqual(self.call("DELETE", "/api/devices/%d" % device_id, token=admin)[0], 200)
        job_id = self.job(tenant_id)
        waiting.join(timeout=20)
        self.assertEqual(got.get("status"), 401, got)
        self.assertNotIn("job", got.get("out") or {})
        self.assertEqual(self.state(job_id), ("in_coda", None), "il lavoro resta in coda per un computer ancora collegato")
        other, other_id = self.device(admin, "PC rimasto")
        self.assertEqual(self.call("POST", "/api/launcher/jobs/claim", {}, other)[1]["job"]["id"], job_id)
        self.assertEqual(self.state(job_id), ("in_corso", other_id))

    def test_anche_nello_stesso_istante_il_lavoro_non_va_a_chi_e_scollegato(self):
        admin, tenant_id = self.new_tenant("Attesa Istante", "completo")
        token, device_id = self.device(admin, "PC")
        con = db.connect()
        device = dict(con.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone())
        tenant = dict(con.execute("SELECT * FROM tenants WHERE id=?", (tenant_id,)).fetchone())
        job_id = self.job(tenant_id)
        real, seen = con.execute, []

        class Late:  # la connessione scollega il computer un attimo dopo il controllo, appena prima che il lavoro venga assegnato
            def execute(self, sql, params=()):
                if sql.startswith("SELECT * FROM jobs") and not seen:
                    seen.append(1)
                    real("UPDATE devices SET revoked=1 WHERE id=?", (device_id,))
                return real(sql, params)
        self.assertIsNone(local.claim(Late(), device, tenant, 0))
        con.close()
        self.assertEqual((seen, self.state(job_id)), ([1], ("in_coda", None)))

    def test_un_launcher_chiuso_mentre_aspettava_non_riceve_il_lavoro(self):
        admin, tenant_id = self.new_tenant("Attesa Chiusa", "completo")
        token, device_id = self.device(admin, "PC che si spegne")
        u = urllib.parse.urlsplit(self.base)
        body = json.dumps({"wait": 8}).encode()
        s = socket.create_connection((u.hostname, u.port), timeout=10)
        s.sendall(b"POST /api/launcher/jobs/claim HTTP/1.1\r\nHost: %s\r\nAuthorization: Bearer %s\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n"
                  % (u.netloc.encode(), token.encode(), len(body)) + body)
        time.sleep(1)
        s.close()  # il launcher viene chiuso a metà dell'attesa
        time.sleep(0.6)
        job_id = self.job(tenant_id)
        time.sleep(2.5)
        self.assertEqual(self.state(job_id), ("in_coda", None), "nessuno ha preso il lavoro al posto di un computer che non c'è più")
        con = db.connect()
        seen = con.execute("SELECT last_seen FROM devices WHERE id=?", (device_id,)).fetchone()["last_seen"]
        con.close()
        self.assertLess(seen, time.time() - 2, "un launcher chiuso non continua a risultare acceso")
        # chi invece resta in linea il lavoro lo riceve, anche se arriva mentre aspetta
        got = {}
        waiting = threading.Thread(target=lambda: got.update(self.call("POST", "/api/launcher/jobs/claim", {"wait": 8}, token)[1]))
        later = self.job(tenant_id)
        waiting.start()
        waiting.join(timeout=20)
        self.assertEqual(got["job"]["id"], job_id)
        self.assertEqual(self.state(later), ("in_coda", None))

    def test_chi_resta_in_linea_riceve_il_lavoro_che_arriva_durante_l_attesa(self):
        admin, tenant_id = self.new_tenant("Attesa Viva", "completo")
        token, device_id = self.device(admin, "PC acceso")
        got = {}
        waiting = threading.Thread(target=lambda: got.update(self.call("POST", "/api/launcher/jobs/claim", {"wait": 8}, token)[1]))
        waiting.start()
        time.sleep(1.5)
        job_id = self.job(tenant_id)
        waiting.join(timeout=20)
        self.assertEqual(got["job"]["id"], job_id)
        self.assertEqual(self.state(job_id), ("in_corso", device_id))

    def test_nella_finestra_il_launcher_non_dice_di_premere_ctrl_c(self):
        admin, tenant_id = self.new_tenant("Attesa Finestra", "completo")
        token, device_id = self.device(admin, "PC con la finestra")
        L.HOME = Path(tempfile.mkdtemp(prefix="sfb-home-"))
        self.addCleanup(shutil.rmtree, str(L.HOME), True)
        lines, stop = [], threading.Event()
        t = threading.Thread(target=L.run, args=({"server": self.base, "public_key": local.public_key(), "token": token, "pacchetti": {}}, stop, lines.append, 1, 3600), daemon=True)
        t.start()
        for _ in range(200):
            if any("In ascolto" in str(l) for l in lines):
                break
            time.sleep(0.05)
        stop.set()
        t.join(8)
        heard = [str(l) for l in lines if "In ascolto" in str(l)]
        self.assertEqual(len(heard), 1, lines)
        self.assertIn("Lascia aperta questa finestra", heard[0])
        self.assertNotIn("Ctrl", heard[0], "nella finestra non c'è nessun Ctrl+C da premere")


if __name__ == "__main__":
    unittest.main()
