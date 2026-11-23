"""Pacchetto locale «video»: monta clip, immagini e musica con FFmpeg seguendo un piano preparato dal server.

Lo stesso file gira sul computer del cliente (avviato dal launcher) e sul server (montaggio «cloud»).
Usa solo la libreria standard di Python e i programmi ffmpeg e ffprobe. Nessuna chiave, nessuna API.
"""
import json
import os
import subprocess

MAX_SECONDS = 600


class WorkerError(Exception):
    pass


def probe(ffprobe, path):
    """Durata in secondi e presenza di una traccia audio."""
    try:
        out = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", path],
                             capture_output=True, text=True, timeout=60)
        data = json.loads(out.stdout or "{}")
        duration = float((data.get("format") or {}).get("duration") or 0)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise WorkerError("Non riesco a leggere il file «%s»." % os.path.basename(path))
    kinds = [s.get("codec_type") for s in data.get("streams") or []]
    if "video" not in kinds and "audio" not in kinds:
        raise WorkerError("Il file «%s» non contiene video né audio leggibili." % os.path.basename(path))
    return {"duration": duration, "audio": "audio" in kinds, "video": "video" in kinds}


def encoders(ffmpeg):
    try:
        out = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return set()
    return {line.split()[1] for line in out.splitlines() if len(line.split()) > 1 and line.startswith(" ")}


def build_command(plan, paths, infos, ffmpeg, out_path, have=None):
    """Riga di comando di FFmpeg per il piano. Ritorna (argomenti, durata totale in secondi)."""
    w, h, fps = int(plan["width"]), int(plan["height"]), int(plan.get("fps") or 30)
    args, chains, labels, total = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"], [], "", 0.0
    for i, item in enumerate(plan["items"]):
        if item["kind"] == "immagine":
            secs, has_audio = float(item.get("seconds") or 3), False
            args += ["-loop", "1", "-t", "%.3f" % secs, "-i", paths[i]]
        else:
            full = infos[i]["duration"]
            secs = min(full, float(item["seconds"])) if item.get("seconds") else full
            if secs <= 0:
                raise WorkerError("La clip «%s» non ha una durata leggibile." % item["asset"]["name"])
            has_audio = infos[i]["audio"]
            args += ["-t", "%.3f" % secs, "-i", paths[i]]
        chains.append("[%d:v]scale=%d:%d:force_original_aspect_ratio=decrease,pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=black,"
                      "setsar=1,fps=%d,format=yuv420p,trim=duration=%.3f,setpts=PTS-STARTPTS[v%d]" % (i, w, h, w, h, fps, secs, i))
        if has_audio:
            chains.append("[%d:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,apad,atrim=duration=%.3f,"
                          "asetpts=PTS-STARTPTS[a%d]" % (i, secs, i))
        else:
            chains.append("anullsrc=r=44100:cl=stereo,atrim=duration=%.3f[a%d]" % (secs, i))
        labels += "[v%d][a%d]" % (i, i)
        total += secs
    if total > MAX_SECONDS:
        raise WorkerError("Il video supererebbe i %d minuti: riduci le clip." % (MAX_SECONDS // 60))
    n = len(plan["items"])
    chains.append("%sconcat=n=%d:v=1:a=1[v][a]" % (labels, n))
    audio = "[a]"
    if plan.get("music"):
        args += ["-stream_loop", "-1", "-i", paths[n]]
        chains.append("[%d:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,volume=%.2f,atrim=duration=%.3f[m]" % (
            n, float(plan.get("music_volume") or 0.35), total))
        chains.append("[a][m]amix=inputs=2:duration=first:normalize=0[mix]")
        audio = "[mix]"
    have = encoders(ffmpeg) if have is None else have
    video_codec = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"] if "libx264" in have else ["-c:v", "mpeg4", "-q:v", "5"]
    args += ["-filter_complex", ";".join(chains), "-map", "[v]", "-map", audio] + video_codec + [
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", "-t", "%.3f" % total,
        "-progress", "pipe:1", "-nostats", out_path]
    return args, total


def video_monta(args, env):
    plan = args["plan"]
    ffmpeg, ffprobe = env.tool("ffmpeg"), env.tool("ffprobe")
    if not ffmpeg or not ffprobe:
        raise WorkerError("FFmpeg non è installato su questo computer.")
    paths = [env.fetch(item["asset"]) for item in plan["items"]]
    infos = [probe(ffprobe, p) if item["kind"] != "immagine" else None for p, item in zip(paths, plan["items"])]
    for item, info in zip(plan["items"], infos):
        if info and not info["video"]:
            raise WorkerError("«%s» non contiene una traccia video." % item["asset"]["name"])
    if plan.get("music"):
        paths.append(env.fetch(plan["music"]))
    out_path = os.path.join(env.workdir, "montaggio.mp4")
    command, total = build_command(plan, paths, infos, ffmpeg, out_path)
    env.progress(1, "avvio di FFmpeg")
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    last = 0
    for line in proc.stdout:
        if line.startswith("out_time_ms=") and total > 0:
            try:
                pct = int(min(99, int(line.split("=", 1)[1]) / 10000.0 / total))
            except ValueError:
                continue
            if pct >= last + 4:
                last = pct
                env.progress(pct, "montaggio")
    err = proc.stderr.read()
    code = proc.wait()
    proc.stdout.close()
    proc.stderr.close()
    if code != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) < 1000:
        raise WorkerError("FFmpeg non è riuscito a montare il video: %s" % (err.strip().splitlines() or ["errore sconosciuto"])[-1][:300])
    name = "".join(c if c.isalnum() or c in " -_" else "_" for c in str(plan.get("title") or "video"))[:60].strip() or "video"
    return {"text": "Video «%s» montato: %d elementi, %.0f secondi, %dx%d." % (plan.get("title") or name, len(plan["items"]), total,
                                                                             int(plan["width"]), int(plan["height"])),
            "file": out_path, "file_name": name + ".mp4"}


def check(env):
    """Prova vera sul computer: monta 2 secondi di video di prova e misura la velocità. Ritorna (ok, messaggio)."""
    import time
    ffmpeg = env.tool("ffmpeg")
    if not ffmpeg:
        return False, "FFmpeg non trovato"
    out = os.path.join(env.workdir, "prova.mp4")
    codec = ["-c:v", "libx264", "-preset", "veryfast"] if "libx264" in encoders(ffmpeg) else ["-c:v", "mpeg4"]
    start = time.time()
    try:
        r = subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=1080x1920:rate=30:duration=2",
                            "-f", "lavfi", "-i", "sine=frequency=440:duration=2"] + codec + ["-pix_fmt", "yuv420p", "-c:a", "aac", out],
                           capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return False, "FFmpeg non risponde"
    took = max(time.time() - start, 0.01)
    if r.returncode != 0 or not os.path.exists(out):
        return False, "FFmpeg non riesce a creare un video MP4: %s" % (r.stderr.strip().splitlines() or ["errore"])[-1][:160]
    speed = 2.0 / took
    if speed < 0.25:
        return False, "troppo lento: un minuto di video verticale richiederebbe circa %d minuti" % round(1 / speed)
    return True, "un minuto di video verticale richiede circa %d secondi" % max(1, round(60 / speed))


TOOLS = {"video_monta": video_monta}
