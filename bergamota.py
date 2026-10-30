#!/usr/bin/env python3
"""Bergamota: conversor e caixa de ferramentas de arquivos para Ubuntu, sem cliques: arraste, toque uma tecla e solte.
Tudo roda localmente. Visão geral do código em AGENTS.md."""
import math, shutil, subprocess, sys, threading
from pathlib import Path

IMG = {"png", "jpg", "jpeg", "webp", "tiff", "tif", "bmp", "gif", "avif", "ico", "heic", "heif", "svg"}
READ_ONLY_IMG = {"heic": "jpg", "heif": "jpg", "svg": "png"}  # só entrada: o que fazer quando a ferramenta devolve o formato
AUD = {"mp3", "m4a", "wav", "flac", "ogg", "opus", "aiff", "wma"}
VID = {"mp4", "mov", "mkv", "webm", "avi", "wmv"}
DOC = {"docx", "doc", "odt", "rtf", "txt", "html"}
SHEET = {"xlsx", "xls", "ods", "csv"}
SLIDE = {"pptx", "ppt", "odp"}
SUB = {"srt", "vtt"}
ARC = {"zip", "tar", "gz", "tgz", "bz2", "xz"}


def out_path(src, ext, suffix=""):
    """Arquivo ao lado do original, sem sobrescrever."""
    return unique_path(src.with_name(f"{src.stem}{suffix}.{ext}"))


def unique_path(base):
    p, i = base, 1
    while p.exists():
        p = base.with_name(f"{base.stem} ({i}){base.suffix}")
        i += 1
    return p


def run(*cmd):
    if not shutil.which(cmd[0]):
        raise RuntimeError(f"'{cmd[0]}' não instalado: sudo apt install {cmd[0]}")
    subprocess.run(cmd, check=True, capture_output=True)


SVG_MIN_SIDE = 2048  # SVG não tem tamanho real: renderiza grande para não sair borrado


def pixbuf_png(data, svg=False):
    """Decodifica pelo GdkPixbuf (HEIC, AVIF, SVG) e devolve PNG em bytes, com transparência e orientação aplicadas."""
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
    loader = GdkPixbuf.PixbufLoader()
    if svg:
        def grow(ld, w, h):
            k = max(1, SVG_MIN_SIDE / max(w, h, 1))
            ld.set_size(round(w * k), round(h * k))
        loader.connect("size-prepared", grow)
    loader.write(data)
    loader.close()
    pix = loader.get_pixbuf().apply_embedded_orientation()
    return pix.save_to_bufferv("png", [], [])[1]


def enable_pixbuf_formats():
    """O Pillow daqui não lê HEIC, AVIF nem SVG: registra os três pelo GdkPixbuf, e todas as ferramentas de
    imagem passam a aceitá-los. AVIF também grava; HEIC e SVG só entram (não há codificador instalado)."""
    import io
    from PIL import Image, PngImagePlugin, features
    Image.init()

    def reader(name, svg=False):
        class PixbufFile(PngImagePlugin.PngImageFile):
            format = name

            def __init__(self, fp, filename=None):
                try:
                    png = pixbuf_png(fp.read(), svg)
                except GLib.Error as ex:
                    raise SyntaxError(str(ex))
                super().__init__(io.BytesIO(png))
        return PixbufFile

    def save_avif(im, fp, _filename):
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf
        if not any(f.get_name() == "avif" and f.is_writable() for f in GdkPixbuf.Pixbuf.get_formats()):
            raise ValueError("este sistema não grava AVIF: use PNG ou WebP")
        rgba = im.convert("RGBA")
        pix = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(rgba.tobytes()), GdkPixbuf.Colorspace.RGB, True, 8,
                                              rgba.width, rgba.height, rgba.width * 4)
        fp.write(pix.save_to_bufferv("avif", ["quality"], [str(im.encoderinfo.get("quality", 75))])[1])

    if not features.check("avif"):
        Image.register_open("AVIF", reader("AVIF"), lambda head: head[4:12] in (b"ftypavif", b"ftypavis"))
        Image.register_save("AVIF", save_avif)
        Image.register_extension("AVIF", ".avif")
    Image.register_open("HEIF", reader("HEIF"), lambda head: head[4:8] == b"ftyp" and head[8:12] in (
        b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"))
    Image.register_open("SVG", reader("SVG", svg=True), lambda head: head.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(
        (b"<svg", b"<?xml", b"<!--", b"<!DOCTYPE svg")))


def flatten(im):
    """Fundo branco no lugar da transparência: convert("RGB") sozinho a deixaria preta."""
    from PIL import Image
    if im.mode in ("RGB", "L"):
        return im.convert("RGB")
    rgba = im.convert("RGBA")
    return Image.alpha_composite(Image.new("RGBA", rgba.size, "white"), rgba).convert("RGB")


def pil(src, ext, quality=95, scale=1.0):
    from PIL import Image
    im = Image.open(src)
    if scale != 1.0:
        im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))))
    if ext in ("jpg", "pdf", "bmp") and im.mode not in ("RGB", "L"):
        im = flatten(im)
    dst = out_path(src, ext, "" if (quality, scale) == (95, 1.0) else "-min")
    im.save(dst, quality=quality)
    return dst


def ffmpeg(src, ext, *args, suffix=""):
    dst = out_path(src, ext, suffix)
    run("ffmpeg", "-y", "-i", str(src), *args, str(dst))
    return dst


def fmt_tc(t):
    """Segundos -> 'm:ss.cc' (ou 'h:mm:ss.cc')."""
    cs = round(max(0, t) * 100)
    h, rest = divmod(cs, 360000)
    m, rest = divmod(rest, 6000)
    s = f"{m:02d}:{rest / 100:05.2f}" if h else f"{m}:{rest / 100:05.2f}"
    return f"{h}:{s}" if h else s


def parse_tc(text):
    """'1:02.5', '62.5', '0:01:02.50' -> segundos; None se inválido."""
    try:
        parts = [float(p) for p in text.strip().replace(",", ".").split(":")]
    except ValueError:
        return None
    if not 1 <= len(parts) <= 3 or any(p < 0 for p in parts):
        return None
    return sum(p * 60 ** i for i, p in enumerate(reversed(parts)))


def probe_video(src):
    """(largura, altura, fps, duração) via ffprobe; largura/altura como aparecem (vídeo de celular girado 90°)."""
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height,avg_frame_rate:stream_side_data=rotation:format=duration",
                          "-of", "json", str(src)], check=True, capture_output=True, text=True).stdout
    import json
    j = json.loads(out)
    st = j["streams"][0]
    num, den = (int(x) for x in st.get("avg_frame_rate", "30/1").split("/"))
    w, h = st["width"], st["height"]
    if any(abs(int(sd.get("rotation", 0))) % 180 == 90 for sd in st.get("side_data_list", [])):
        w, h = h, w
    return w, h, (num / den if num and den else 30.0), float(j["format"]["duration"])


def video_codec(dst, audio=None):
    """Recodificação de alta qualidade para o contêiner de dst; audio='copy' mantém o áudio original."""
    if dst.suffix == ".webm":
        return ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-c:a", audio or "libopus"]
    return ["-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            *(["-c:a", "copy"] if audio else ["-c:a", "aac", "-b:a", "192k"]), "-movflags", "+faststart"]


def video_out(src, suffix):
    e = src.suffix.lower().lstrip(".")
    return out_path(src, e if e in ("mp4", "mov", "mkv", "webm") else "mp4", suffix)


def trim_cmd(src, start, end, dst):
    """Corte preciso no quadro: busca na entrada e recodifica (cópia de stream só corta em keyframe)."""
    return ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{end - start:.3f}", *video_codec(dst),
            str(dst)]


def crop_cmd(src, box, dst):
    """Recorta o quadro (x0, y0, x1, y1) em lados pares (exigência do yuv420p); áudio copiado sem mexer
    quando o contêiner não muda."""
    x0, y0, x1, y1 = box
    w, h = (x1 - x0) // 2 * 2, (y1 - y0) // 2 * 2
    same = src.suffix.lower() == dst.suffix.lower()
    return ["ffmpeg", "-y", "-i", str(src), "-vf", f"crop={w}:{h}:{x0 // 2 * 2}:{y0 // 2 * 2}",
            *video_codec(dst, "copy" if same else None), str(dst)]


def redact_cmd(src, boxes, style, color, dst):
    """Cobre cada caixa (x0, y0, x1, y1, início, fim) só no seu intervalo: cor sólida (drawbox) ou desfoque
    (recorte desfocado sobreposto). Lados pares por causa do yuv420p; áudio copiado se o contêiner não muda."""
    rgb = "0x%02X%02X%02X" % tuple(round(c * 255) for c in color)
    chain, cur = [], "[0:v]"
    for i, (x0, y0, x1, y1, t0, t1) in enumerate(boxes):
        x, y = int(x0) // 2 * 2, int(y0) // 2 * 2
        w, h = max(2, (int(round(x1)) - x) // 2 * 2), max(2, (int(round(y1)) - y) // 2 * 2)
        on = f"enable='between(t,{t0:.3f},{t1:.3f})'"
        if style == "blur":
            r = max(1, min(40, min(w, h) // 4))  # o boxblur limita o raio a meio lado do plano de cor (metade da caixa)
            chain.append(f"{cur}split[a{i}][b{i}];[b{i}]crop={w}:{h}:{x}:{y},boxblur={r}:3[c{i}];"
                         f"[a{i}][c{i}]overlay={x}:{y}:{on}[v{i}]")
        else:
            chain.append(f"{cur}drawbox=x={x}:y={y}:w={w}:h={h}:color={rgb}:t=fill:{on}[v{i}]")
        cur = f"[v{i}]"
    same = src.suffix.lower() == dst.suffix.lower()
    return ["ffmpeg", "-y", "-i", str(src), "-filter_complex", ";".join(chain), "-map", cur, "-map", "0:a?",
            *video_codec(dst, "copy" if same else None), str(dst)]


def loudness(src):
    """Medição EBU R128 do loudnorm: {'input_i', 'input_tp', 'input_lra', 'input_thresh', 'target_offset', …}."""
    import json
    err = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(src), "-map", "0:a:0", "-af", "loudnorm=print_format=json",
                          "-f", "null", "-"], capture_output=True, text=True, check=True).stderr
    return {k: float(v) for k, v in json.loads(err[err.rindex("{"):err.rindex("}") + 1]).items()
            if k != "normalization_type"}


def media_duration(src):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                 str(src)], check=True, capture_output=True, text=True).stdout)


def audio_rate(src):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=sample_rate",
                          "-of", "csv=p=0", str(src)], capture_output=True, text=True).stdout.strip()
    return out or "48000"


def cover_args(dst):
    """Copia a capa só nos formatos que guardam imagem; WAV e Ogg falhariam com uma faixa de vídeo."""
    return ["-map", "0:v?", "-c:v", "copy"] if dst.suffix in (".mp3", ".m4a", ".flac") else []


AUDIO_QUALITY = {".mp3": ["-q:a", "2"], ".m4a": ["-b:a", "192k"], ".ogg": ["-q:a", "6"], ".opus": ["-b:a", "160k"]}


def normalize_cmd(src, target, lra, tp, m, dst):
    """Segunda passada do loudnorm com a medição m: ganho linear quando dá, sem mexer na dinâmica.
    O loudnorm sobe a taxa para 192 kHz; -ar devolve a original. Capa (mp3/m4a/flac) copiada."""
    af = (f"loudnorm=I={target}:LRA={lra}:TP={tp}:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
          f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    return ["ffmpeg", "-y", "-i", str(src), "-map", "0:a:0", *cover_args(dst), "-af", af,
            "-ar", audio_rate(src), *AUDIO_QUALITY.get(dst.suffix, []), str(dst)]


def normalize_out(src):
    return audio_out(src, "-normalizado")


def waveform(src, bars, channels=False):
    """Pico de cada um dos `bars` trechos do áudio, de 0 a 1 (relativo ao maior).
    channels=True: [esquerdo, direito] na mesma escala (mono vira os dois iguais)."""
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(src), "-map", "0:a:0", "-ac", "2" if channels else "1",
                          "-ar", "8000", "-f", "s16le", "-"], capture_output=True, check=True).stdout
    a = np.abs(np.frombuffer(raw, np.int16).astype(np.float32)).reshape(-1, 2 if channels else 1)
    if not a.size:
        return [[0.0] * bars] * 2 if channels else [0.0] * bars
    peaks = np.array([c.max(axis=0) if c.size else np.zeros(a.shape[1]) for c in np.array_split(a, bars)])
    peaks = peaks / max(peaks.max(), 1)
    return [list(peaks[:, 0]), list(peaks[:, 1])] if channels else list(peaks[:, 0])


VIZ_SHAPES = [("Paisagem · 16:9", (1280, 720)), ("Retrato · 9:16", (720, 1280)), ("Quadrado · 1:1", (1080, 1080))]
VIZ_OUT = ["-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest",
           "-movflags", "+faststart"]


def visualizer_cmd(src, size, color, image, dst):
    """MP4 do áudio: onda animada (showwaves) na cor dada sobre preto, ou a imagem parada (image) inteira no quadro."""
    W, H = size
    if image:
        fit = f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black,format=yuv420p"
        return ["ffmpeg", "-y", "-loop", "1", "-framerate", "5", "-i", str(image), "-i", str(src), "-vf", fit,
                "-map", "0:v", "-map", "1:a", "-tune", "stillimage", *VIZ_OUT, str(dst)]
    rgb = "0x%02X%02X%02X" % tuple(round(c * 255) for c in color)
    ww, wh = int(W * 0.9) // 2 * 2, int(H * 0.5) // 2 * 2
    # dynaudnorm só no desenho: gravação baixa ainda mostra onda (o áudio do vídeo segue o original).
    # draw=full + rgb24 antes do pad: sem isso o alfa da onda escurece a cor.
    graph = (f"[0:a]dynaudnorm=f=50:g=5,showwaves=s={ww}x{wh}:mode=cline:colors={rgb}:draw=full:scale=sqrt:rate=30,format=rgb24,"
             f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black,format=yuv420p[v]")
    return ["ffmpeg", "-y", "-i", str(src), "-filter_complex", graph, "-map", "[v]", "-map", "0:a", *VIZ_OUT, str(dst)]


def audio_cover(src):
    """Capa embutida (mp3/m4a/flac) como imagem PIL, ou None."""
    import io
    from PIL import Image
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", str(src), "-map", "0:v:0", "-frames:v", "1", "-f", "image2pipe",
                          "-vcodec", "png", "-"], capture_output=True).stdout
    return Image.open(io.BytesIO(out)).convert("RGB") if out else None


def audio_out(src, suffix):
    e = src.suffix.lower().lstrip(".")
    return out_path(src, e if e in AUD - {"wma"} else "m4a", suffix)


def audio_trim_cmd(src, start, end, dst):
    """Trecho exato do áudio (recodifica: cópia de stream só corta em quadro do codec). Capa copiada."""
    return ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{end - start:.3f}", "-map", "0:a:0",
            *cover_args(dst), *AUDIO_QUALITY.get(dst.suffix, []), str(dst)]


def silent_ends(src, dur, pad=0.05):
    """(início, fim) sem o silêncio das pontas (abaixo de -45 dB por 0,1 s+); o meio não é mexido.
    Deixa pad segundos de folga para não comer o começo do som."""
    import re
    err = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(src), "-map", "0:a:0", "-af",
                          "silencedetect=noise=-45dB:d=0.1", "-f", "null", "-"], capture_output=True, text=True,
                         check=True).stderr
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", err)]
    start, end = 0.0, dur
    if starts and starts[0] < 0.01 and ends:
        start = max(0.0, ends[0] - pad)
    if starts and (len(ends) < len(starts) or ends[-1] >= dur - 0.01) and starts[-1] > start:
        end = min(dur, starts[-1] + pad)
    return round(start, 2), round(end, 2)


def audio_channels(src):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=channels",
                          "-of", "csv=p=0", str(src)], capture_output=True, text=True).stdout.strip()
    return int(out or 2)


def channel_filter(n, vl, vr, out):
    """pan do ffmpeg: out 'stereo', 'mono' ou 'L'/'R' (um canal sozinho, para ouvir). vl/vr de 0 a 1.
    Origem mono vira os dois lados; com mais de 2 canais usa a frente esquerda/direita. A mistura mono
    divide pela soma dos volumes: com um lado em 0 o outro sai inteiro, sem cair pela metade."""
    L, R = "c0", "c1" if n > 1 else "c0"
    if out == "stereo":
        return f"pan=stereo|c0={vl:.3f}*{L}|c1={vr:.3f}*{R}"
    if out in ("L", "R"):
        return f"pan=mono|c0={vl if out == 'L' else vr:.3f}*{L if out == 'L' else R}"
    s = max(vl + vr, 1)
    return f"pan=mono|c0={vl / s:.3f}*{L}+{vr / s:.3f}*{R}"


def channels_cmd(src, af, dst, seconds=None):
    """Aplica o pan; seconds limita (prévia). Capa copiada."""
    cut = ["-t", str(seconds)] if seconds else []
    return ["ffmpeg", "-y", "-i", str(src), *cut, "-map", "0:a:0", *cover_args(dst), "-af", af,
            *AUDIO_QUALITY.get(dst.suffix, []), str(dst)]


def bleep_cmd(src, ranges, dur, rate, dst):
    """Troca cada (início, fim) por um bipe de 1 kHz: o som original vira silêncio de verdade, não só fica coberto.
    asetnsamples: o enable do volume vale por bloco; blocos de 64 amostras deixam a borda exata (~1,5 ms)."""
    on = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in ranges)
    graph = (f"[0:a:0]asetnsamples=n=64:p=0,volume=0:enable='{on}'[m];"
             f"sine=f=1000:r={rate}:d={dur:.3f},asetnsamples=n=64:p=0,volume=2,volume=0:enable='not({on})'[s];"
             "[m][s]amix=inputs=2:normalize=0:duration=first[a]")
    return ["ffmpeg", "-y", "-i", str(src), "-filter_complex", graph, "-map", "[a]", *cover_args(dst),
            *AUDIO_QUALITY.get(dst.suffix, []), str(dst)]


def pdf_pages(src):
    """Número de páginas, ou None se o pdfinfo não conseguir ler (corrompido ou protegido por senha)."""
    import re
    m = re.search(r"^Pages:\s+(\d+)", subprocess.run(["pdfinfo", str(src)], capture_output=True, text=True).stdout, re.M)
    return int(m[1]) if m else None


def pdf_sizes(src):
    """[(largura, altura)] em pontos de cada página como aparece na tela (já com /Rotate)."""
    import re
    n = pdf_pages(src)
    if not n:
        raise ValueError("não deu para ler este PDF")
    out = subprocess.run(["pdfinfo", "-f", "1", "-l", str(n), str(src)], capture_output=True, text=True).stdout
    sizes = re.findall(r"^Page +\d+ size: +([\d.]+) x ([\d.]+)", out, re.M)
    rots = re.findall(r"^Page +\d+ rot: +(\d+)", out, re.M)
    return [(float(h), float(w)) if int(r) % 180 else (float(w), float(h)) for (w, h), r in zip(sizes, rots)]


def transform_page(src, dst, w, h, turn, k):
    """Uma página (visível w×h) girada turn graus no sentido horário e escalada k vezes."""
    W, H = (h, w) if turn % 180 else (w, h)
    move = {0: "", 90: f"0 {w} translate -90 rotate", 180: f"{w} {h} translate 180 rotate",
            270: f"{h} 0 translate 90 rotate"}[turn]
    run("gs", "-q", "-sDEVICE=pdfwrite", "-dNOPAUSE", "-dBATCH", "-dAutoRotatePages=/None", "-dFIXEDMEDIA",
        f"-dDEVICEWIDTHPOINTS={W * k:.2f}", f"-dDEVICEHEIGHTPOINTS={H * k:.2f}", f"-sOutputFile={dst}",
        "-c", f"<</Install {{{k:.5f} {k:.5f} scale {move}}}>> setpagedevice", "-f", str(src))


def organize_pdf(src, pages, same_width, dst):
    """pages = [(índice original, giro)] na ordem final. Só as páginas giradas ou escaladas passam pelo Ghostscript;
    as outras saem como estão. same_width escala tudo para a largura da página mais estreita."""
    import tempfile
    sizes = pdf_sizes(src)
    visible = [sizes[i][::-1] if turn % 180 else sizes[i] for i, turn in pages]
    narrow = min(w for w, _h in visible)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        run("pdfseparate", str(src), str(tmp / "p%d.pdf"))
        parts = []
        for pos, ((i, turn), (vw, _vh)) in enumerate(zip(pages, visible)):
            part = tmp / f"p{i + 1}.pdf"
            k = narrow / vw if same_width else 1
            if turn or abs(k - 1) > 1e-3:
                part, page = tmp / f"t{pos}.pdf", part
                transform_page(page, part, *sizes[i], turn, k)
            parts.append(str(part))
        run("pdfunite", *parts, str(dst))
    return dst


def fmt_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1000 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}".replace(".", ",")
        n /= 1000


def file_kind(src):
    e = src.suffix.lower().lstrip(".")
    return ("gif" if e == "gif" else "image" if e in IMG else "video" if e in VID else "audio" if e in AUD
            else "pdf" if e == "pdf" else "other")


def image_bytes(im, fmt, level):
    """A imagem codificada: level é a qualidade (JPEG/WebP) ou o número de cores (PNG, mantém transparência)."""
    import io
    from PIL import Image
    buf = io.BytesIO()
    if fmt == "PNG":
        im.convert("RGBA").quantize(colors=level, method=Image.Quantize.FASTOCTREE).save(buf, "PNG", optimize=True)
    elif fmt == "WEBP":
        im.save(buf, "WEBP", quality=level, method=6)
    else:
        im.convert("RGB").save(buf, "JPEG", quality=level, optimize=True, progressive=True)
    return buf.getvalue()


def compress_image(src, preset, scale, target):
    """Mesmo formato quando dá (BMP/TIFF/AVIF sem compressão útil aqui viram PNG se transparentes, senão JPG).
    Com target, escolhe o maior nível que cabe e só então reduz as dimensões."""
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(Image.open(src))
    e = src.suffix.lower().lstrip(".")
    alpha = im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info
    ext = e if e in ("png", "jpg", "jpeg", "webp") else "webp" if alpha else "jpg"
    fmt = {"png": "PNG", "webp": "WEBP"}.get(ext, "JPEG")
    levels = (256, 128, 64, 32, 16) if fmt == "PNG" else tuple(range(90, 15, -5))
    if scale < 1:
        im = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))), Image.LANCZOS)
    dst = out_path(src, ext, "-comprimido")
    if not target:
        dst.write_bytes(image_bytes(im, fmt, ((256, 64) if fmt == "PNG" else (80, 55))[preset]))
        return dst
    best = None
    for k in (1, 0.8, 0.64, 0.5, 0.35, 0.25):
        small = im if k == 1 else im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))), Image.LANCZOS)
        for level in levels:
            data = image_bytes(small, fmt, level)
            if best is None or len(data) < len(best):
                best = data
            if len(data) <= target:
                dst.write_bytes(data)
                return dst
    dst.write_bytes(best)  # não coube: entrega o menor que conseguiu
    return dst


def compress_gif(src, preset, scale):
    dst = out_path(src, "gif", "-comprimido")
    size = f"scale=iw*{scale}:-2:flags=lanczos," if scale < 1 else ""
    run("ffmpeg", "-y", "-i", str(src), "-vf",
        f"{size}split[a][b];[a]palettegen=max_colors={(128, 48)[preset]}[p];[b][p]paletteuse", str(dst))
    return dst


def compress_video(src, preset, target):
    """x264; com target calcula a taxa de bits da duração (o resultado fica perto, não exato)."""
    dst = out_path(src, "mp4", "-comprimido")
    audio = (128, 96)[preset]
    rate = ["-crf", str((26, 32)[preset])]
    if target:
        kbps = max(100, int(target * 8 * 0.95 / 1000 / max(media_duration(src), 0.1)) - audio)
        rate = ["-b:v", f"{kbps}k", "-maxrate", f"{kbps * 3 // 2}k", "-bufsize", f"{kbps * 2}k"]
    run("ffmpeg", "-y", "-i", str(src), "-c:v", "libx264", *rate, "-preset", "medium", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", f"{audio}k", "-movflags", "+faststart", str(dst))
    return dst


def compress_audio(src, preset, target):
    """Formato com perdas fica como está; WAV/FLAC/AIFF/WMA viram M4A. A capa sai: é parte do peso."""
    e = src.suffix.lower().lstrip(".")
    dst = out_path(src, e if e in ("mp3", "m4a", "ogg", "opus") else "m4a", "-comprimido")
    kbps = (128, 64)[preset]
    if target:
        kbps = min(320, max(32, int(target * 8 * 0.97 / 1000 / max(media_duration(src), 0.1))))
    run("ffmpeg", "-y", "-i", str(src), "-map", "0:a:0", "-vn", "-b:a", f"{kbps}k", str(dst))
    return dst


def compress_pdf(src, preset, target):
    """Ghostscript: reduz as imagens do PDF; com target, usa a qualidade mais alta que couber."""
    dst = out_path(src, "pdf", "-comprimido")
    for setting in ("printer", "ebook", "screen") if target else (("ebook", "screen")[preset],):
        run("gs", "-q", "-sDEVICE=pdfwrite", "-dNOPAUSE", "-dBATCH", "-dCompatibilityLevel=1.5",
            f"-dPDFSETTINGS=/{setting}", f"-sOutputFile={dst}", str(src))
        if not target or dst.stat().st_size <= target:
            break
    return dst


def compress_other(src, preset):
    """Qualquer outro arquivo: ZIP, ou .tar.xz (mais lento, menor) no modo forte."""
    import tarfile
    import zipfile
    if preset:
        dst = out_path(src, "tar.xz")
        with tarfile.open(dst, "w:xz") as tar:
            tar.add(src, arcname=src.name)
    else:
        dst = out_path(src, "zip")
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            z.write(src, src.name)
    return dst


def compress_file(src, preset, scale=1, target=None):
    """Comprime sem tocar no original. Devolve o arquivo novo, ou None se não ficou menor (nada é mantido)."""
    kind = file_kind(src)
    dst = (compress_image(src, preset, scale, target) if kind == "image" else
           compress_gif(src, preset, scale) if kind == "gif" else
           compress_video(src, preset, target) if kind == "video" else
           compress_audio(src, preset, target) if kind == "audio" else
           compress_pdf(src, preset, target) if kind == "pdf" else compress_other(src, preset))
    if dst.stat().st_size >= src.stat().st_size:
        dst.unlink()
        return None
    return dst


META_READONLY = {"System", "File"}  # dados do próprio arquivo, não etiquetas gravadas nele
META_TAGS_GROUP = "Etiquetas"  # etiquetas lidas e gravadas pelo ffmpeg


def exiftool_list(flag):
    return set(subprocess.run(["exiftool", flag], capture_output=True, text=True, check=True).stdout.split())


def meta_route(src):
    """Como gravar: 'exif' (exiftool), 'ffmpeg' (áudio/vídeo que o exiftool não grava, como MP3 e MKV) ou None."""
    if src.suffix.upper().lstrip(".") in exiftool_list("-listwf"):
        return "exif"
    return "ffmpeg" if file_kind(src) in ("audio", "video") else None


def read_metadata(src):
    """[(grupo, campo, valor)]; na rota ffmpeg as etiquetas vêm do ffprobe e as do exiftool ficam só as técnicas."""
    import json
    out = subprocess.run(["exiftool", "-j", "-G1", "-a", "-s", "-e", str(src)], capture_output=True, text=True,
                         check=True).stdout
    fields = [(*k.split(":", 1), v) for k, v in json.loads(out)[0].items() if ":" in k and not k.startswith("ExifTool:")]
    if meta_route(src) != "ffmpeg":
        return fields
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags:stream_tags", "-of", "json",
                            str(src)], capture_output=True, text=True, check=True).stdout
    data = json.loads(probe)
    tags = {}
    for part in (*data.get("streams", []), data.get("format", {})):
        tags.update(part.get("tags", {}))
    fields = [f for f in fields if not f[0].startswith(("ID3", "Vorbis"))]
    return [(META_TAGS_GROUP, k, v) for k, v in tags.items()] + fields


def scrub_pdf(path):
    """A edição do exiftool em PDF é reversível: os dados antigos ficam no arquivo. Regravar elimina esse histórico."""
    clean = path.with_name(path.name + ".tmp")
    run("gs", "-q", "-sDEVICE=pdfwrite", "-dNOPAUSE", "-dBATCH", f"-sOutputFile={clean}", str(path))
    clean.replace(path)


def write_metadata(src, edits, removed, dst):
    """edits {'Grupo:Campo': valor}, removed {'Grupo:Campo'}: grava uma cópia em dst."""
    if meta_route(src) == "ffmpeg":
        pairs = [(k.split(":", 1)[1], v) for k, v in edits.items()] + [(k.split(":", 1)[1], "") for k in removed]
        meta = [a for k, v in pairs for a in ("-metadata", f"{k}={v}", "-metadata:s:a:0", f"{k}={v}")]
        run("ffmpeg", "-y", "-i", str(src), "-map", "0", "-c", "copy", *meta, str(dst))
        return dst
    run("exiftool", "-q", "-q", *[f"-{k}={v}" for k, v in edits.items()], *[f"-{k}=" for k in removed],
        "-o", str(dst), str(src))
    if file_kind(src) == "pdf":
        scrub_pdf(dst)
    return dst


def strip_metadata(src, dst):
    """Remove tudo o que dá. Fotos mantêm orientação e perfil de cor: sem eles a imagem gira ou muda de cor."""
    if meta_route(src) == "ffmpeg":
        run("ffmpeg", "-y", "-i", str(src), "-map", "0:a" if file_kind(src) == "audio" else "0", "-c", "copy",
            "-map_metadata", "-1", "-map_metadata:s:a", "-1", "-map_chapters", "-1", "-bitexact", str(dst))
        return dst
    keep = ["--icc_profile:all", "-tagsfromfile", "@", "-Orientation"] if file_kind(src) == "image" else []
    run("exiftool", "-q", "-q", "-all=", *keep, "-o", str(dst), str(src))
    if file_kind(src) == "pdf":
        scrub_pdf(dst)
    return dst


def extract(src):
    """Abre ZIP e TAR (também .tar.gz etc.) numa pasta nova; .gz, .bz2 e .xz soltos viram o arquivo de dentro."""
    import bz2
    import gzip
    import lzma
    import tarfile
    import zipfile
    folder = unique_path(src.parent / src.name.split(".")[0])
    if zipfile.is_zipfile(src):
        with zipfile.ZipFile(src) as z:
            z.extractall(folder)
        return folder
    if tarfile.is_tarfile(src):
        with tarfile.open(src) as t:
            t.extractall(folder, filter="data")  # recusa caminhos que saem da pasta
        return folder
    inner = src.with_suffix("")
    dst = unique_path(inner if inner.suffix else inner.with_name(inner.name + ".extraido"))
    opener = {".gz": gzip.open, ".bz2": bz2.open, ".xz": lzma.open}[src.suffix.lower()]
    with opener(src) as f, open(dst, "wb") as out:
        shutil.copyfileobj(f, out)
    return dst


def page_ranges(text, total):
    """'1-3, 5, 8-' em grupos de páginas (a partir de 0); ValueError com a causa se algo estiver fora do PDF."""
    import re
    groups = []
    for part in filter(None, (p.strip() for p in text.split(","))):
        m = re.fullmatch(r"(\d+)\s*(-\s*(\d*))?", part)
        if not m:
            raise ValueError(f"não entendi “{part}”")
        first = int(m[1])
        last = first if m[2] is None else int(m[3]) if m[3] else total
        if not 1 <= first <= last <= total:
            raise ValueError(f"“{part}” está fora do PDF, que tem {total} páginas")
        groups.append(list(range(first - 1, last)))
    if not groups:
        raise ValueError("escreva ao menos um intervalo")
    return groups


def split_groups(total, every):
    return [list(range(i, min(i + every, total))) for i in range(0, total, every)]


def split_pdf(src, groups):
    """Um PDF por grupo numa pasta nova ao lado do original; as páginas saem intactas."""
    import tempfile
    folder = unique_path(src.parent / f"{src.stem}-dividido")
    folder.mkdir()
    with tempfile.TemporaryDirectory() as tmp:
        run("pdfseparate", str(src), f"{tmp}/p%d.pdf")
        for n, group in enumerate(groups, 1):
            run("pdfunite", *[f"{tmp}/p{i + 1}.pdf" for i in group], str(folder / f"{src.stem}-{n}.pdf"))
    return folder


def read_cues(src):
    """[(início, fim, [linhas])] de SRT ou VTT. Aceita UTF-8 e o Windows-1252 de legendas antigas."""
    import re
    raw = src.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", "replace")
    cues = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n")):
        lines = block.strip("\n").split("\n")
        at = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if at is None:  # cabeçalho WEBVTT, NOTE, STYLE
            continue
        start, end = lines[at].split("-->")[:2]
        cues.append((start.strip(), end.split()[0] if end.split() else "", lines[at + 1:]))
    return cues


def cue_time(t, sep):
    """Tempo no formato do destino: SRT usa vírgula e sempre traz as horas; VTT pode omiti-las."""
    t = t.replace(",", ".")
    return (t if t.count(":") == 2 else f"00:{t}").replace(".", sep)


def convert_subtitles(src, ext):
    import re
    cues = read_cues(src)
    if not cues:
        raise ValueError("nenhuma legenda encontrada no arquivo")
    if ext == "txt":
        said = [re.sub(r"<[^>]+>", "", " ".join(c[2])).strip() for c in cues]
        text = "\n".join(s for i, s in enumerate(said) if s and (i == 0 or s != said[i - 1]))
    elif ext == "srt":
        text = "\n\n".join(f"{i}\n{cue_time(a, ',')} --> {cue_time(b, ',')}\n" + "\n".join(lines)
                           for i, (a, b, lines) in enumerate(cues, 1))
    else:
        text = "WEBVTT\n\n" + "\n\n".join(f"{cue_time(a, '.')} --> {cue_time(b, '.')}\n" + "\n".join(lines)
                                         for a, b, lines in cues)
    dst = out_path(src, ext)
    dst.write_text(text + "\n", encoding="utf-8")
    return dst


def slide_images(src, ext):
    """Uma imagem por slide, via PDF temporário."""
    pdf = office(src, "pdf")
    try:
        return pdf_images(pdf, ext)
    finally:
        pdf.unlink()


def has_audio(src):
    return bool(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                                "-of", "csv=p=0", str(src)], capture_output=True, text=True).stdout.strip())


def atempo_chain(speed):
    """atempo aceita 0,5–2 por estágio com boa qualidade: 4× = 2×2, 0,25× = 0,5×0,5. Mantém o tom."""
    stages = []
    while speed > 2:
        stages.append(2.0); speed /= 2
    while speed < 0.5:
        stages.append(0.5); speed /= 0.5
    return ",".join(f"atempo={x:g}" for x in stages + [speed])


def speed_cmd(src, speed, fps, dst, audio=True):
    """Cópia mais lenta/rápida; quadros reamostrados para o fps original (sem vídeo 120 fps nem 7,5 fps)."""
    a = ["-filter:a", atempo_chain(speed)] if audio else ["-an"]
    return ["ffmpeg", "-y", "-i", str(src), "-filter:v", f"setpts=PTS/{speed:g},fps={fps:.3f}", *a,
            *video_codec(dst), str(dst)]


def join_cmd(clips, W, H, fps, dst):
    """clips = [(arquivo, duração, tem_áudio)] na ordem. Cada um cabe inteiro (com tarja) na tela do primeiro;
    clipe sem som ganha silêncio para o áudio não sair de sincronia."""
    W, H = W // 2 * 2, H // 2 * 2
    audio = any(a for _f, _d, a in clips)
    inputs, graph, pairs = [], [], ""
    for i, (f, dur, a) in enumerate(clips):
        inputs += ["-i", str(f)]
        graph.append(f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,"
                     f"setsar=1,fps={fps:.3f},format=yuv420p[v{i}]")
        pairs += f"[v{i}]"
        if audio:
            src = f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo,apad," if a else \
                "anullsrc=r=48000:cl=stereo,"
            graph.append(f"{src}atrim=0:{dur:.3f}[a{i}]")
            pairs += f"[a{i}]"
    graph.append(f"{pairs}concat=n={len(clips)}:v=1:a={int(audio)}[v]" + ("[a]" if audio else ""))
    maps = ["-map", "[v]"] + (["-map", "[a]"] if audio else [])
    return ["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(graph), *maps, *video_codec(dst), str(dst)]


def video_frame(src, t, size=None):
    """Quadro em t segundos (já girado como o player mostra) como imagem PIL; size=(l, a) reduz."""
    import io
    from PIL import Image
    vf = ["-vf", f"scale={size[0]}:{size[1]}"] if size else []
    out = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(t, 0):.3f}", "-i", str(src), "-frames:v", "1", *vf,
                          "-f", "image2pipe", "-vcodec", "ppm", "-"], check=True, capture_output=True).stdout
    return Image.open(io.BytesIO(out))


def office(src, ext, infilter=None):
    """LibreOffice grava numa pasta temporária ao lado do arquivo (o snap não enxerga /tmp) e sobrescreveria
    um arquivo de mesmo nome; o resultado vem para um nome livre."""
    import tempfile
    cmd = ["libreoffice", "--headless"] + ([f"--infilter={infilter}"] if infilter else [])
    with tempfile.TemporaryDirectory(dir=src.parent, prefix="bergamota-") as tmp:
        run(*cmd, "--convert-to", ext, "--outdir", tmp, str(src))
        made = next(Path(tmp).iterdir(), None)
        if made is None:
            raise RuntimeError("o LibreOffice não gerou o arquivo")
        dst = out_path(src, made.suffix.lstrip("."))
        shutil.move(made, dst)
    return dst


def pdf_images(src, ext):
    """Uma imagem por página numa pasta nova (o pdftoppm sobrescreveria imagens de mesmo nome)."""
    folder = unique_path(src.parent / f"{src.stem}-imagens")
    folder.mkdir()
    run("pdftoppm", "-r", "300", "-png" if ext == "png" else "-jpeg", str(src), str(folder / "pagina"))
    return folder


def multi(fn):
    """Marca uma ferramenta que recebe a lista inteira de arquivos de uma vez."""
    fn.multi = True
    return fn


def same_kind(files):
    """Vários arquivos do mesmo tipo (imagens, vídeos ou PDFs): libera as ferramentas de lote."""
    kinds = [f.suffix.lower().lstrip(".") for f in files]
    return len(files) > 1 and any(all(k in group for k in kinds) for group in (IMG, VID, {"pdf"}))


def run_tool(files, fn, on_done=lambda: None):
    if getattr(fn, "multi", False):
        fn(files)
        on_done()
    else:
        run_job(files, fn, on_done)


def options(src, many=False):
    """[(seção, rótulo, função(src) -> destino)] para o tipo do arquivo.
    many=True: vários arquivos do mesmo tipo soltos juntos (libera colagem, unir vídeos e unir PDFs)."""
    e = src.suffix.lower().lstrip(".")
    o = []
    conv = lambda label, f: o.append(("Converter", label, f))
    tool = lambda label, f: o.append(("Ferramentas", label, f))
    if src.is_file():
        if e not in ARC:
            tool("Comprimir", multi(lambda files: GLib.idle_add(open_compressor, files) and None))
        tool("Metadados", lambda s: GLib.idle_add(open_metadata, s) and None)
        if e in IMG:
            for t in ("png", "jpg", "webp", "avif", "tiff", "bmp", "gif", "pdf"):
                if t != e and not (t == "jpg" and e == "jpeg"):
                    conv(t.upper(), lambda s, t=t: pil(s, t))
            tool("Reduzir 50%", lambda s: pil(s, READ_ONLY_IMG.get(e, e), scale=0.5))
            tool("Editar", lambda s: GLib.idle_add(open_editor, s) and None)
            tool("Anotar", lambda s: GLib.idle_add(open_annotator, s) and None)
            tool("Fundo", lambda s: GLib.idle_add(open_background, s) and None)
            tool("Recortar", lambda s: GLib.idle_add(open_cropper, s) and None)
            if many:
                tool("Colagem", multi(lambda files: GLib.idle_add(open_collage, files) and None))
        elif e in AUD:
            for t in ("mp3", "m4a", "wav", "flac", "ogg", "opus", "aiff", "wma"):
                if t != e:
                    conv(t.upper(), lambda s, t=t: ffmpeg(s, t))
            tool("Cortar", lambda s: GLib.idle_add(open_audio_trimmer, s) and None)
            tool("Normalizar volume", lambda s: GLib.idle_add(open_normalizer, s) and None)
            tool("Canais", lambda s: GLib.idle_add(open_channels, s) and None)
            tool("Bipe", lambda s: GLib.idle_add(open_bleeper, s) and None)
            tool("Visualizador", lambda s: GLib.idle_add(open_visualizer, s) and None)
        elif e in VID:
            for t in ("mp4", "mov", "mkv", "webm", "avi", "wmv", "gif"):
                if t != e:
                    conv(t.upper(), lambda s, t=t: ffmpeg(s, t))
            conv("MP3 (áudio)", lambda s: ffmpeg(s, "mp3", "-vn"))
            tool("Cortar", lambda s: GLib.idle_add(open_trimmer, s) and None)
            tool("Recortar", lambda s: GLib.idle_add(open_video_cropper, s) and None)
            tool("Velocidade", lambda s: GLib.idle_add(open_speed, s) and None)
            tool("Quadros", lambda s: GLib.idle_add(open_snapshots, s) and None)
            tool("Ocultar", lambda s: GLib.idle_add(open_redactor, s) and None)
            if many:
                tool("Unir vídeos", multi(lambda files: GLib.idle_add(open_join, files) and None))
        elif e == "pdf":
            conv("PNG (300 DPI)", lambda s: pdf_images(s, "png"))
            conv("JPG (300 DPI)", lambda s: pdf_images(s, "jpg"))
            conv("TXT", lambda s: (run("pdftotext", str(s), str(out_path(s, "txt"))), s.parent)[1])
            conv("DOCX", lambda s: office(s, "docx:MS Word 2007 XML", "writer_pdf_import"))
            tool("Organizar", lambda s: GLib.idle_add(open_organizer, s) and None)
            tool("Dividir", lambda s: GLib.idle_add(open_splitter, s) and None)
            if many:
                tool("Unir PDFs", multi(lambda files: GLib.idle_add(open_pdf_merge, files) and None))
        elif e in DOC | SHEET | SLIDE:
            targets = {**dict.fromkeys(DOC, ("pdf", "docx", "odt", "txt", "html")),
                       **dict.fromkeys(SHEET, ("pdf", "xlsx", "ods", "csv")),
                       **dict.fromkeys(SLIDE, ("pdf", "pptx", "odp"))}[e]
            for t in targets:
                if t != e:
                    conv(t.upper(), lambda s, t=t: office(s, t))
            if e in SLIDE:
                conv("PNG (slides)", lambda s: slide_images(s, "png"))
                conv("JPG (slides)", lambda s: slide_images(s, "jpg"))
        elif e in SUB:
            for t in ("srt", "vtt", "txt"):
                if t != e:
                    conv(t.upper(), lambda s, t=t: convert_subtitles(s, t))
        if e in ARC:
            tool("Extrair", extract)
    if e not in ARC:
        tool("Compactar ZIP", lambda s: Path(shutil.make_archive(
            str(out_path(s, "zip").with_suffix("")), "zip", s.parent, s.name)))
    return o


# ---------------- Editar foto ----------------
# (chave, rótulo, mínimo, máximo, passo, formato) por seção; 0 é sempre neutro.
ADJUSTMENTS = {
    "Luz": [("exposure", "Exposição", -3, 3, 0.01, "{:+.2f} EV"), ("brightness", "Brilho", -100, 100, 1, "{:+.0f}"),
            ("contrast", "Contraste", -100, 100, 1, "{:+.0f}"), ("highlights", "Realces", -100, 100, 1, "{:+.0f}"),
            ("shadows", "Sombras", -100, 100, 1, "{:+.0f}"), ("whites", "Brancos", -100, 100, 1, "{:+.0f}"),
            ("blacks", "Pretos", -100, 100, 1, "{:+.0f}"), ("gamma", "Gama", -100, 100, 1, "{:+.0f}")],
    "Cor": [("temperature", "Temperatura", -100, 100, 1, "{:+.0f}"), ("tint", "Matiz verde/magenta", -100, 100, 1, "{:+.0f}"),
            ("saturation", "Saturação", -100, 100, 1, "{:+.0f}"), ("vibrance", "Vivacidade", -100, 100, 1, "{:+.0f}"),
            ("hue", "Tom", -180, 180, 1, "{:+.0f}°")],
    "Detalhe": [("clarity", "Claridade", -100, 100, 1, "{:+.0f}"), ("dehaze", "Remover névoa", -100, 100, 1, "{:+.0f}"),
                ("sharpness", "Nitidez", 0, 100, 1, "{:.0f}"), ("denoise", "Redução de ruído", 0, 100, 1, "{:.0f}"),
                ("grain", "Granulação", 0, 100, 1, "{:.0f}"), ("vignette", "Vinheta", -100, 100, 1, "{:+.0f}")],
}


def box_blur(a, r):
    """Desfoque de caixa separável (H x W [x C]) via soma acumulada; 3 passadas ~ gaussiano."""
    import numpy as np
    r = max(1, int(r))
    for _ in range(3):
        for axis in (0, 1):
            m = np.moveaxis(a, axis, 0)
            c = np.cumsum(np.concatenate([np.repeat(m[:1], r + 1, 0), m, np.repeat(m[-1:], r, 0)]), 0,
                          dtype=np.float32)
            a = np.moveaxis((c[2 * r + 1:] - c[:-2 * r - 1]) / (2 * r + 1), 0, axis)
    return a


def adjust(img, v):
    """Aplica os ajustes v {chave: valor} numa imagem float32 RGB 0..1 (H x W x 3)."""
    import numpy as np
    g = lambda k: v.get(k, 0) / 100
    a = img.astype(np.float32, copy=True)
    h, w = a.shape[:2]
    scale = max(h, w) / 1000  # raios proporcionais ao tamanho: prévia e original ficam iguais
    lum = lambda x: x @ np.array([0.2126, 0.7152, 0.0722], np.float32)

    if g("denoise"):
        a += (box_blur(a, 1.5 * scale) - a) * g("denoise") * 0.8
    a *= 2 ** v.get("exposure", 0)
    if g("whites") or g("blacks"):
        bp, wp = -g("blacks") * 0.15, 1 - g("whites") * 0.25
        a = (a - bp) / (wp - bp)
    if g("contrast"):
        a = (a - 0.5) * (1 + g("contrast")) + 0.5
    a += g("brightness") * 0.2
    if g("gamma"):
        a = np.clip(a, 0, None) ** (2 ** -g("gamma"))
    if g("highlights") or g("shadows"):
        L = np.clip(lum(a), 0, 1)[..., None]
        hi, sh = np.clip((L - 0.5) * 2, 0, 1) ** 2, np.clip(1 - L * 2, 0, 1) ** 2
        a += (g("highlights") * hi + g("shadows") * sh) * 0.35
    if g("temperature") or g("tint"):
        a *= np.array([1 + g("temperature") * 0.15, 1 - g("tint") * 0.12, 1 - g("temperature") * 0.15], np.float32)
    if v.get("hue", 0):  # rotação no espaço YIQ
        t = math.radians(v["hue"])
        to = np.array([[0.299, 0.587, 0.114], [0.596, -0.274, -0.322], [0.211, -0.523, 0.312]], np.float32)
        rot = np.array([[1, 0, 0], [0, math.cos(t), -math.sin(t)], [0, math.sin(t), math.cos(t)]], np.float32)
        a = a @ (np.linalg.inv(to) @ rot @ to).T.astype(np.float32)
    if g("saturation") or g("vibrance"):
        L = lum(a)[..., None]
        r, gg, bb = a[..., 0], a[..., 1], a[..., 2]
        sat = (np.maximum(np.maximum(r, gg), bb) - np.minimum(np.minimum(r, gg), bb))[..., None]
        a = L + (a - L) * (1 + g("saturation")) * (1 + g("vibrance") * np.clip(1 - sat * 1.5, 0, 1))
    if g("dehaze") > 0:
        t = 1 - g("dehaze") * 0.6 * np.clip(box_blur(a.min(-1), 15 * scale), 0, 0.95)[..., None]
        a = (a - (1 - t)) / np.maximum(t, 0.1)
    elif g("dehaze") < 0:
        a += (0.85 - a) * -g("dehaze") * 0.5
    if g("clarity"):
        L = lum(a)[..., None]
        a += (L - box_blur(L, 25 * scale)) * g("clarity") * 0.8
    if g("sharpness"):
        a += (a - box_blur(a, max(1, scale))) * g("sharpness") * 1.5
    if g("vignette"):
        yy, xx = np.ogrid[-1:1:complex(h), -1:1:complex(w)]
        a *= (1 - g("vignette") * 0.7 * np.clip((xx ** 2 + yy ** 2) / 2, 0, 1) ** 1.2)[..., None]
    if g("grain"):
        rng = np.random.default_rng(7)
        a += rng.standard_normal((h, w, 1), np.float32) * g("grain") * 0.08
    return np.clip(a, 0, 1)


def selftest():
    import tempfile
    from PIL import Image
    d = Path(tempfile.mkdtemp())
    src = d / "a.png"
    Image.new("RGBA", (10, 10), "orange").save(src)
    labels = [l for _, l, _ in options(src)]
    assert "JPG" in labels and "PNG" not in labels, labels
    assert pil(src, "jpg").name == "a.jpg" and pil(src, "jpg").name == "a (1).jpg"
    zipped = dict((l, f) for _, l, f in options(src))["Compactar ZIP"](src)
    assert zipped.name == "a.zip" and zipped.exists(), zipped
    assert segment(0, -100, 4, 60) == 0 and segment(100, 0, 4, 60) == 1
    assert segment(-100, 0, 4, 60) == 3 and segment(5, 5, 4, 60) is None
    assert segment(-30, -100, 8, 60) == 0 and segment(60, -100, 8, 60) == 1
    import numpy as np
    img = np.random.default_rng(1).random((40, 60, 3), np.float32) * 0.5
    assert np.allclose(adjust(img, {}), img, atol=1e-6)
    assert np.allclose(adjust(img, {"exposure": 1}), img * 2, atol=1e-5)
    gray = adjust(img, {"saturation": -100})
    assert np.allclose(gray[..., 0], gray[..., 1], atol=1e-4)
    assert np.allclose(adjust(img, {"hue": 360}), img, atol=1e-4)
    assert np.allclose(box_blur(np.ones((9, 9), np.float32), 2), 1)
    photo = pil_to_surface(Image.new("RGB", (200, 100), "white"))
    items = [dict(kind=k, pts=[(20, 20), (80, 60)], color=(1, 0, 0), w=3, text="Oi", size=20, n=1,
                  ) for k in ("magnifier", "arrow", "rect", "ellipse", "pen", "marker")]
    items += [dict(kind="text", pts=[(120, 10)], color=(0, 0, 1), w=3, text="Oi", size=20),
              dict(kind="number", pts=[(150, 70)], color=(0, 0.5, 0), w=3, n=7),
              dict(kind="image", pts=[(170, 70)], color=(0, 0, 0), w=3, img=photo, size=(20, 10))]
    out = np.asarray(surface_to_pil(render_annotated(photo, items)))
    assert out.shape == (100, 200, 3) and (out[40, 50] != 255).any() and (out[0, 199] == 255).all()
    assert seg_dist((5, 5), (0, 0), (10, 0)) == 5 and seg_dist((-3, 4), (0, 0), (10, 0)) == 5
    assert canvas_size(100, 50, 10, None) == (120, 70) and canvas_size(100, 50, 10, 1) == (120, 120)
    assert canvas_size(100, 50, 0, 9 / 16) == (100, 178)
    framed = render_framed(Image.new("RGB", (40, 20), "red"), ("solid", (0, 0, 1)), 10, 4, 3, 20, 1)
    assert framed.size == (60, 60) and framed.getpixel((30, 30)) == (255, 0, 0)
    assert framed.getpixel((2, 2))[2] > 200 and framed.getpixel((10, 20)) != (255, 0, 0)  # canto arredondado
    r = drag_handle((0, 0, 100, 100), "se", (60, 30), None, 200, 100)
    assert r == (0, 0, 60, 30)
    r = drag_handle((0, 0, 100, 100), "se", (300, 150), 1, 200, 100)
    assert r == (0, 0, 100, 100)  # proporção 1:1 limitada pela altura da imagem
    r = drag_handle((50, 0, 150, 100), "w", (0, 50), 2, 200, 100)
    assert r[2] == 150 and abs((r[2] - r[0]) / (r[3] - r[1]) - 2) < 1e-9 and r[1] >= 0 and r[3] <= 100
    r = drag_handle((0, 0, 100, 100), "nw", (90, 99), None, 200, 100)
    assert r[2] - r[0] >= CROP_MIN and r[3] - r[1] >= CROP_MIN
    assert move_rect((0, 0, 50, 50), 500, -20, 200, 100) == (150, 0, 200, 50)
    r = ratio_rect(16 / 9, 200, 100)
    assert abs(r[0] - 100 + 88.888) < 0.01 and r[1] == 0 and r[3] == 100 and abs(r[2] - r[0] - 177.777) < 0.01
    assert collage_cells("grid", 3, 2, 200, 200, 0, 0) == [(0, 0, 100, 100), (100, 0, 100, 100), (0, 100, 100, 100)]
    cells = collage_cells("featured", 3, 2, 210, 100, 10, 0)
    assert cells[0][2] > cells[1][2] and cells[1][3] == cells[2][3] == 45 and cells[2][1] == 55
    assert collage_grid("row", 4, 2) == (4, 1) and collage_grid("column", 4, 2) == (1, 4)
    assert collage_height("grid", 4, 2, 210, None, 10, 0, 2) == 110  # 2 linhas de 50 px + 10 de espaço
    col = render_collage([Image.new("RGB", (40, 20), "red"), Image.new("RGB", (20, 40), "blue")], "row", 2, 100,
                         None, "fill", 0, 0, 0, (1, 1, 1))
    assert col.getpixel((10, 10)) == (255, 0, 0) and col.getpixel((90, 10)) == (0, 0, 255)
    assert [l for _s, l, _f in options(src, True)].count("Colagem") == 1 and "Colagem" not in labels
    assert fmt_tc(4) == "0:04.00" and fmt_tc(61.256) == "1:01.26" and fmt_tc(3725.5) == "1:02:05.50"
    assert parse_tc("1:01.26") == 61.26 and parse_tc("4,5") == 4.5 and parse_tc("1:02:05.5") == 3725.5
    assert parse_tc("abc") is None and parse_tc("-1") is None
    cmd = trim_cmd(Path("v.mp4"), 1, 2.5, Path("v-cortado.mp4"))
    assert cmd[cmd.index("-ss") + 1] == "1.000" and cmd[cmd.index("-t") + 1] == "1.500" and "libx264" in cmd
    assert "libvpx-vp9" in trim_cmd(Path("v.webm"), 0, 1, Path("v-cortado.webm"))
    vid = d / "v.mp4"
    run("ffmpeg", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=25:duration=4", "-f", "lavfi", "-i",
        "sine=duration=4", "-shortest", "-pix_fmt", "yuv420p", str(vid))
    assert probe_video(vid)[:3] == (160, 120, 25.0)
    dst = d / "v-cortado.mp4"
    subprocess.run(trim_cmd(vid, 1, 2.5, dst), check=True, capture_output=True)
    assert abs(probe_video(dst)[3] - 1.5) < 0.1, probe_video(dst)
    assert "Cortar" in [l for _s, l, _f in options(vid)] and "Recortar" in [l for _s, l, _f in options(vid)]
    assert crop_cmd(vid, (11, 9, 92, 70), dst)[5] == "crop=80:60:10:8"
    cropped = d / "v-recortado.mp4"
    subprocess.run(crop_cmd(vid, (10, 10, 91, 70), cropped), check=True, capture_output=True)
    assert probe_video(cropped)[:2] == (80, 60)
    streams = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0",
                              str(cropped)], capture_output=True, text=True).stdout.split()
    assert streams == ["video", "audio"], streams
    assert video_frame(vid, 1, (40, 30)).size == (40, 30)
    assert atempo_chain(4) == "atempo=2,atempo=2" and atempo_chain(0.25) == "atempo=0.5,atempo=0.5"
    assert atempo_chain(1.5) == "atempo=1.5"
    fast = d / "v-2x.mp4"
    subprocess.run(speed_cmd(vid, 2, 25, fast), check=True, capture_output=True)
    assert abs(probe_video(fast)[3] - 2) < 0.15 and has_audio(fast), probe_video(fast)
    mute = d / "mudo.mp4"
    run("ffmpeg", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=25:duration=2", "-pix_fmt", "yuv420p", str(mute))
    slow = d / "mudo-0.5x.mp4"
    subprocess.run(speed_cmd(mute, 0.5, 25, slow, has_audio(mute)), check=True, capture_output=True)
    assert not has_audio(mute) and abs(probe_video(slow)[3] - 4) < 0.15
    tall = d / "alto.mov"
    run("ffmpeg", "-f", "lavfi", "-i", "testsrc=size=120x160:rate=30:duration=1", "-pix_fmt", "yuv420p", str(tall))
    joined = d / "unidos.mp4"
    clips = [(f, probe_video(f)[3], has_audio(f)) for f in (vid, mute, tall)]
    subprocess.run(join_cmd(clips, 160, 120, 25, joined), check=True, capture_output=True)
    w, h, _f, dur = probe_video(joined)
    assert (w, h) == (160, 120) and abs(dur - 7) < 0.2 and has_audio(joined), (w, h, dur)
    assert same_kind([vid, tall]) and not same_kind([vid, src]) and not same_kind([vid])
    assert "Unir vídeos" in [l for _s, l, _f in options(vid, True)]
    hidden = d / "oculto.mp4"
    subprocess.run(redact_cmd(vid, [(10, 10, 61, 41, 1, 2), (80, 60, 120, 100, 0, 4)], "solid", (1, 0, 0), hidden),
                   check=True, capture_output=True)
    assert probe_video(hidden)[:2] == probe_video(vid)[:2] and has_audio(hidden)
    on, off = video_frame(hidden, 1.5).convert("RGB"), video_frame(hidden, 0.5).convert("RGB")
    r, g, _b = on.getpixel((30, 25))
    assert r > 200 and g < 60, on.getpixel((30, 25))
    assert off.getpixel((30, 25)) == video_frame(vid, 0.5).convert("RGB").getpixel((30, 25)) or \
        off.getpixel((30, 25))[1] > 60  # fora do intervalo: sem caixa
    blurred = d / "desfocado.mkv"
    subprocess.run(redact_cmd(vid, [(0, 0, 160, 120, 0, 4)], "blur", (0, 0, 0), blurred), check=True,
                   capture_output=True)
    from PIL import ImageFilter, ImageStat
    edges = lambda f: ImageStat.Stat(video_frame(f, 1).convert("L").filter(ImageFilter.FIND_EDGES)).mean[0]
    sharp, soft = edges(vid), edges(blurred)
    assert soft < sharp / 2, (soft, sharp)
    quiet = d / "baixo.wav"
    run("ffmpeg", "-f", "lavfi", "-i", "sine=f=440:d=3,volume=0.05", "-ar", "44100", str(quiet))
    m = loudness(quiet)
    assert m["input_i"] < -40, m
    for ext in ("wav", "mp3"):
        dst = normalize_out(quiet).with_suffix("." + ext)
        subprocess.run(normalize_cmd(quiet, -16, 11, -1.5, m, dst), check=True, capture_output=True)
        out = loudness(dst)
        assert abs(out["input_i"] + 16) < 1 and out["input_tp"] <= -1, (ext, out)
        assert audio_rate(dst) == "44100", audio_rate(dst)
    assert abs(media_duration(quiet) - 3) < 0.05
    for size, image in (((320, 180), None), ((180, 320), d / "foto.png")):
        if image:
            Image.new("RGB", (64, 48), (200, 30, 30)).save(image)
        viz = d / f"viz-{size[0]}.mp4"
        subprocess.run(visualizer_cmd(quiet, size, (1, 0.4, 0), image, viz), check=True, capture_output=True)
        w, h, _f, dur = probe_video(viz)
        assert (w, h) == size and abs(dur - 3) < 0.3 and has_audio(viz), (w, h, dur)
    assert audio_cover(quiet) is None
    import cairo
    book = d / "cmd" / "commands.json"
    assert load_commands(book) == []
    save_commands([{"name": "Enviar", "icon": "bolt", "command": "cp {arquivo} {pasta}/copia.txt", "terminal": False},
                   {"name": "", "command": "x"}, {"name": "Sem ícone", "icon": "nao-existe", "command": "true"}, "lixo"], book)
    saved = load_commands(book)
    assert [c["name"] for c in saved] == ["Enviar", "Sem ícone"] and saved[1]["icon"] == "terminal"
    book.write_text("lixo")
    assert load_commands(book) == []
    evil = d / "cmd" / "a b'$(touch hackeado).txt"
    evil.write_text("oi")
    assert run_command(saved[0], evil) is None and (evil.parent / "copia.txt").read_text() == "oi"
    assert not (evil.parent / "hackeado").exists(), "nome de arquivo virou comando"
    assert fill_command("wc -l", d / "x y.txt") == "wc -l", "acrescentou o caminho"
    assert fill_command("echo {nome}", d / "x y.txt") == "echo 'x y.txt'"
    fail = run_command({"command": "echo ruim >&2; exit 3", "terminal": False}, evil)
    assert fail and "ruim" in fail
    assert run_command({"command": "true", "terminal": False}, evil) is None
    argv = terminal_argv(d, ["sh", "-c", "ls"])
    assert argv[-4:] == ["--", "sh", "-c", "ls"] and "--working-directory" in argv
    plain = terminal_argv(d)
    assert plain[-2:] == ["--working-directory", str(d)] and "--" not in plain
    assert "--new-window" in plain or "ptyxis" not in plain[0], "ptyxis ignora a pasta"
    items = action_items([evil], saved)
    assert isinstance(items[-1][0], CustomLabel) and label_icon(items[-2][0]) == "bolt" and len(items) == 6
    assert "Mostrar na pasta" not in [l for l, _f in items]
    assert label_icon("Mover para…") == "move" and not isinstance(items[0][0], CustomLabel)
    for kind in COMMAND_ICONS:
        probe = cairo.ImageSurface(cairo.FORMAT_ARGB32, 20, 20)
        tool_icon(cairo.Context(probe), kind, (1, 1, 1, 1))
        assert any(probe.get_data()), f"ícone vazio: {kind}"
    wheel = cairo.ImageSurface(cairo.FORMAT_ARGB32, BASE, BASE)
    draw_wheel(cairo.Context(wheel), BASE, "actions", [CustomLabel("Meu", "star"), "Terminal aqui"], 0, True, True, (1, 0.4, 0))
    px = lambda x, y: bytes(wheel.get_data()[(y * wheel.get_stride() + x * 4):(y * wheel.get_stride() + x * 4 + 4)])
    # duas fatias, 0 no topo: perto da borda interna da fatia do usuário só há vidro; a outra é sólida
    assert px(BASE // 2 - 60, 100)[3] < 200 and px(BASE // 2 + 60, BASE - 100)[3] == 255, (px(150, 100), px(270, 320))
    cfg = d / "cfg" / "keys.json"
    assert load_keys(cfg) == DEFAULT_KEYS
    swapped = assign_key(DEFAULT_KEYS, "tools", "alt")
    assert swapped == {"convert": "shift", "tools": "alt", "actions": "ctrl"}
    assert assign_key(DEFAULT_KEYS, "tools", "super") == {"convert": "shift", "tools": "super", "actions": "alt"}
    assert assign_key(DEFAULT_KEYS, "tools", "ctrl") == DEFAULT_KEYS
    save_keys(swapped, cfg)
    assert load_keys(cfg) == swapped
    for bad in ('{"convert": "shift", "tools": "shift", "actions": "alt"}', '{"convert": "f1"}', "lixo", "[]", "null"):
        cfg.write_text(bad)
        assert load_keys(cfg) == DEFAULT_KEYS, bad
    box, there = d / "box", d / "destino"
    box.mkdir()
    there.mkdir()
    (box / "a.txt").write_text("um")
    (box / "pasta").mkdir()
    (box / "pasta" / "b.txt").write_text("dois")
    assert transfer([box / "a.txt", box / "pasta"], there, False) == []
    assert (there / "a.txt").read_text() == "um" and (there / "pasta" / "b.txt").exists() and (box / "a.txt").exists()
    assert transfer([box / "a.txt"], there, False) == [] and (there / "a (1).txt").exists(), "sobrescreveu ao copiar"
    (box / "a.txt").write_text("novo")
    assert transfer([box / "a.txt"], there, True) == []
    assert not (box / "a.txt").exists() and (there / "a (2).txt").read_text() == "novo"
    assert (there / "a.txt").read_text() == "um", "sobrescreveu ao mover"
    assert transfer([box / "pasta"], there, True) == [] and (there / "pasta (1)" / "b.txt").exists()
    (box / "c.txt").write_text("fica")
    assert transfer([box / "c.txt"], box, True) == [] and (box / "c.txt").exists(), "mover para a mesma pasta"
    assert len(transfer([there], there / "pasta", False)) == 1 and len(transfer([there], there / "pasta", True)) == 1
    assert there.is_dir(), "pasta movida para dentro de si"
    assert [l for l, _f in action_items([box / "c.txt"])][:2] == ["Copiar para…", "Mover para…"]
    enable_pixbuf_formats()
    photo = d / "av.png"
    Image.new("RGB", (64, 48), (0, 0, 255)).save(photo)
    av = pil(photo, "avif")
    assert av.suffix == ".avif" and Image.open(av).format == "AVIF" and Image.open(av).size == (64, 48)
    assert Image.open(av).convert("RGB").getpixel((5, 5))[2] > 200
    assert pil(av, "png").suffix == ".png" and "PNG" in [l for _s, l, _f in options(av)]
    see_through = d / "tr.png"
    glass = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
    glass.paste((255, 0, 0, 255), (0, 0, 10, 20))
    glass.save(see_through)
    clear_av = Image.open(pil(see_through, "avif")).convert("RGBA")
    assert clear_av.getpixel((15, 10))[3] < 10 and clear_av.getpixel((3, 10))[3] > 240, "AVIF perdeu transparência"
    vec = d / "icone.svg"
    vec.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10"><rect width="10" height="10" '
                   'fill="#00f"/></svg>')
    drawn = Image.open(vec)
    assert drawn.format == "SVG" and drawn.size == (SVG_MIN_SIDE, SVG_MIN_SIDE // 2) and drawn.mode == "RGBA"
    white = Image.open(pil(vec, "jpg")).convert("RGB").getpixel((SVG_MIN_SIDE - 50, 50))
    assert min(white) > 240, ("fundo transparente virou escuro", white)
    assert Image.open(pil(vec, "png")).convert("RGBA").getpixel((SVG_MIN_SIDE - 50, 50))[3] == 0
    assert pil(vec, "png", scale=0.5).name == "icone-min.png"
    assert "Reduzir 50%" in [l for _s, l, _f in options(vec)] and "PNG" in [l for _s, l, _f in options(vec)]
    assert "HEIC" not in [l for _s, l, _f in options(photo)], "HEIC não tem codificador aqui"
    assert min(load_rgb(vec)[50, SVG_MIN_SIDE - 50]) > 0.9, "transparência escura nas ferramentas"
    import bz2
    import gzip
    import io
    import lzma
    import tarfile
    import zipfile
    bag = d / "bag"
    bag.mkdir()
    for name, opener in (("n.txt.gz", gzip.open), ("n.txt.bz2", bz2.open), ("m.txt.xz", lzma.open)):
        with opener(bag / name, "wb") as f:
            f.write(b"oi")
        out = extract(bag / name)
        assert out.read_bytes() == b"oi" and out.suffix == ".txt", out
    assert extract(bag / "n.txt.gz").name == "n (2).txt", "sobrescreveu"
    with gzip.open(bag / "semext.gz", "wb") as f:
        f.write(b"oi")
    assert extract(bag / "semext.gz").name == "semext.extraido"
    with tarfile.open(bag / "p.tar.gz", "w:gz") as t:
        t.add(bag / "n.txt", arcname="n.txt")
    assert (extract(bag / "p.tar.gz") / "n.txt").read_bytes() == b"oi"
    with zipfile.ZipFile(bag / "z.zip", "w") as z:
        z.writestr("dentro.txt", "oi")
    assert (extract(bag / "z.zip") / "dentro.txt").read_text() == "oi"
    assert extract(bag / "z.zip").name == "z (1)", "pasta sobrescrita"
    with tarfile.open(bag / "mau.tar", "w") as t:
        info = tarfile.TarInfo("../fora.txt")
        info.size = 2
        t.addfile(info, io.BytesIO(b"oi"))
    try:
        extract(bag / "mau.tar")
        raise AssertionError("tar saiu da pasta")
    except tarfile.TarError:
        pass
    assert not (bag / "fora.txt").exists()
    if shutil.which("exiftool"):
        shot = d / "exif.jpg"
        Image.new("RGB", (40, 30), "red").save(shot)
        run("exiftool", "-q", "-overwrite_original", "-Make=Canon", "-Artist=Fulano", "-GPSLatitude=23.5",
            "-GPSLatitudeRef=S", "-Orientation#=6", str(shot))
        names = {(g, t): v for g, t, v in read_metadata(shot)}
        assert names[("IFD0", "Make")] == "Canon" and meta_route(shot) == "exif", names
        assert meta_label("ExifImageWidth") == "Exif Image Width" and meta_label("GPSLatitude") == "GPS Latitude"
        edited = write_metadata(shot, {"IFD0:Make": "Nikon"}, {"IFD0:Artist"}, d / "exif-e.jpg")
        names = {(g, t): v for g, t, v in read_metadata(edited)}
        assert names[("IFD0", "Make")] == "Nikon" and ("IFD0", "Artist") not in names
        assert ("IFD0", "Make") in {(g, t) for g, t, _v in read_metadata(shot)}, "original mexido"
        bare = strip_metadata(shot, d / "exif-s.jpg")
        names = {(g, t) for g, t, _v in read_metadata(bare)}
        assert ("IFD0", "Orientation") in names and ("IFD0", "Artist") not in names
        assert not any(t.startswith("GPS") or t == "Make" for _g, t in names), names
        song = d / "tag.mp3"
        run("ffmpeg", "-f", "lavfi", "-i", "sine=d=1", "-metadata", "title=Velho", "-metadata", "artist=Fulano", str(song))
        assert meta_route(song) == "ffmpeg"
        assert ("Etiquetas", "title", "Velho") in read_metadata(song)
        changed = write_metadata(song, {"Etiquetas:title": "Novo"}, {"Etiquetas:artist"}, d / "tag-e.mp3")
        tags = {t: v for g, t, v in read_metadata(changed) if g == "Etiquetas"}
        assert tags.get("title") == "Novo" and "artist" not in tags, tags
        assert not [t for g, t, _v in read_metadata(strip_metadata(song, d / "tag-s.mp3")) if g == "Etiquetas"]
        doc = d / "doc.pdf"
        Image.new("RGB", (40, 30), "red").save(doc)
        run("exiftool", "-q", "-overwrite_original", "-Author=Fulano", str(doc))
        clean = strip_metadata(doc, d / "doc-s.pdf")
        assert not [t for _g, t, _v in read_metadata(clean) if t == "Author"]
        back = subprocess.run(["exiftool", "-PDF-update:all=", "-o", str(d / "doc-v.pdf"), str(clean)],
                              capture_output=True, text=True)
        assert "no previous" in back.stderr + back.stdout, "PDF ainda guarda histórico"
        kept = write_metadata(doc, {"PDF:Author": "Sicrano"}, set(), d / "doc-e.pdf")
        assert ("PDF", "Author", "Sicrano") in read_metadata(kept) or             any(t == "Author" and v == "Sicrano" for _g, t, v in read_metadata(kept)), read_metadata(kept)
        assert meta_route(d / "nota.txt") is None
    from PIL import ImageDraw
    noisy = Image.effect_noise((640, 480), 60).convert("RGB")
    ImageDraw.Draw(noisy).rectangle((100, 100, 300, 300), fill="red")
    pic = d / "foto.jpg"
    noisy.save(pic, quality=98)
    orig = pic.stat().st_size
    bal, strong = compress_file(pic, 0), compress_file(pic, 1)
    assert 0 < strong.stat().st_size < bal.stat().st_size < orig and bal.suffix == ".jpg", (orig, bal, strong)
    assert Image.open(compress_file(pic, 0, 0.5)).size == (320, 240)
    goal = compress_file(pic, 0, 1, orig // 5)
    assert goal.stat().st_size <= orig // 5, (goal.stat().st_size, orig)
    tiny = compress_file(pic, 0, 1, 3000)
    assert tiny.stat().st_size <= 3000 and Image.open(tiny).width < 640
    clear = Image.new("RGBA", (300, 300), (0, 0, 0, 0))
    for x in range(0, 300, 2):
        ImageDraw.Draw(clear).line((x, 0, 300 - x, 300), fill=(x % 255, 200 - x // 2, x // 3, 255))
    png = d / "t.png"
    clear.save(png)
    small_png = compress_file(png, 1)
    assert small_png.suffix == ".png" and small_png.stat().st_size < png.stat().st_size
    assert Image.open(small_png).convert("RGBA").getpixel((0, 150))[3] in (0, 255)
    long_vid = d / "longo.mp4"
    run("ffmpeg", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=4", "-f", "lavfi", "-i", "sine=d=4",
        "-c:v", "libx264", "-crf", "5", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k", str(long_vid))
    size = long_vid.stat().st_size
    aim = int(size * 0.3)
    v = compress_file(long_vid, 0, 1, aim)
    assert v.stat().st_size < size and v.stat().st_size < aim * 1.25 and has_audio(v), (size, aim, v.stat().st_size)
    wav = d / "voz.wav"
    run("ffmpeg", "-f", "lavfi", "-i", "sine=d=3", str(wav))
    a2 = compress_file(wav, 0)
    assert a2.suffix == ".m4a" and a2.stat().st_size < wav.stat().st_size
    text = d / "nota.txt"
    text.write_text("bergamota " * 2000)
    z, xz = compress_file(text, 0), compress_file(text, 1)
    assert z.suffix == ".zip" and xz.name == "nota.tar.xz" and xz.stat().st_size < text.stat().st_size
    assert shutil.unpack_archive(z, d / "z") is None and (d / "z" / "nota.txt").read_text() == text.read_text()
    (d / "curto.txt").write_text("oi")
    assert compress_file(d / "curto.txt", 0) is None and not (d / "curto.zip").exists(), "arquivo maior não fica"
    assert fmt_size(3400000) == "3,4 MB" and fmt_size(900) == "900 B"
    assert "Comprimir" in [l for _s, l, _f in options(vid)] and "Comprimir" in [l for _s, l, _f in options(text)]
    Image.new("RGB", (60, 80), "red").save(d / "a.pdf", save_all=True,
                                           append_images=[Image.new("RGB", (60, 80), "green")])
    Image.new("RGB", (80, 60), "blue").save(d / "b.pdf")
    assert pdf_pages(d / "a.pdf") == 2 and pdf_pages(d / "b.pdf") == 1
    (d / "ruim.pdf").write_bytes(b"nao sou pdf")
    assert pdf_pages(d / "ruim.pdf") is None
    assert same_kind([d / "a.pdf", d / "b.pdf"]) and not same_kind([d / "a.pdf", vid])
    assert "Unir PDFs" in [l for _s, l, _f in options(d / "a.pdf", True)]
    assert "Unir PDFs" not in [l for _s, l, _f in options(d / "a.pdf")]
    merged = d / "unidos.pdf"
    subprocess.run(["pdfunite", str(d / "b.pdf"), str(d / "a.pdf"), str(merged)], check=True, capture_output=True)
    assert pdf_pages(merged) == 3
    assert subprocess.run(["pdfunite", str(d / "ruim.pdf"), str(d / "a.pdf"), str(d / "x.pdf")],
                          capture_output=True).returncode != 0
    assert pdf_sizes(merged) == [(80, 60), (60, 80), (60, 80)], pdf_sizes(merged)
    assert page_ranges("1-2, 3", 3) == [[0, 1], [2]] and page_ranges("2-", 3) == [[1, 2]]
    assert split_groups(5, 2) == [[0, 1], [2, 3], [4]]
    for bad in ("", "0", "4", "3-1", "a"):
        try:
            page_ranges(bad, 3)
            raise AssertionError(f"aceitou {bad!r}")
        except ValueError:
            pass
    parts = split_pdf(merged, page_ranges("1, 2-3", 3))
    assert [pdf_pages(parts / f"unidos-{i}.pdf") for i in (1, 2)] == [1, 2]
    assert split_pdf(merged, split_groups(3, 1)).name == "unidos-dividido (1)", "pasta sobrescrita"
    srt = d / "leg.srt"
    srt.write_bytes("1\r\n00:00:01,000 --> 00:00:02,500\r\n<i>Olá</i> mundo\r\n\r\n2\r\n00:00:03,000 --> 00:00:04,000\r\n"
                    "Coração\r\nsegunda linha\r\n\r\n".encode("cp1252"))
    vtt = convert_subtitles(srt, "vtt")
    assert vtt.read_text().startswith("WEBVTT\n\n00:00:01.000 --> 00:00:02.500\n<i>Olá</i> mundo"), vtt.read_text()
    assert convert_subtitles(srt, "txt").read_text() == "Olá mundo\nCoração segunda linha\n"
    web = d / "web.vtt"
    web.write_text("WEBVTT\n\nNOTE comentário\n\nid1\n01:02.000 --> 01:03.500 align:start\nOi\n")
    back = convert_subtitles(web, "srt").read_text()
    assert back == "1\n00:01:02,000 --> 00:01:03,500\nOi\n", back
    assert convert_subtitles(convert_subtitles(srt, "vtt"), "srt").name == "leg (1).srt", "sobrescreveu"
    pics = pdf_images(merged, "png")
    assert len(list(pics.iterdir())) == 3 and pdf_images(merged, "png").name == "unidos-imagens (1)"
    big_pdf = d / "fotos.pdf"
    Image.effect_noise((1600, 1200), 80).convert("RGB").save(big_pdf, quality=95, resolution=300)
    light = compress_file(big_pdf, 1)
    assert light.stat().st_size < big_pdf.stat().st_size and pdf_pages(light) == 1

    def page_colors(pdf):
        import io
        out = subprocess.run(["pdftoppm", "-r", "10", "-png", "-singlefile", str(pdf)], capture_output=True).stdout
        return Image.open(io.BytesIO(out)).convert("RGB").getpixel((2, 2))
    org = organize_pdf(merged, [(2, 0), (0, 90), (2, 0)], False, d / "org.pdf")  # verde, azul girada, verde repetida
    assert pdf_sizes(org) == [(60, 80), (60, 80), (60, 80)], pdf_sizes(org)
    assert page_colors(org)[1] > 100, page_colors(org)
    one = organize_pdf(merged, [(1, 180)], False, d / "um.pdf")
    assert pdf_pages(one) == 1 and pdf_sizes(one) == [(60, 80)]
    wide = organize_pdf(merged, [(0, 0), (1, 0)], True, d / "larg.pdf")
    assert [round(w) for w, _h in pdf_sizes(wide)] == [60, 60] and round(pdf_sizes(wide)[0][1]) == 45, pdf_sizes(wide)
    import numpy as np
    tone = d / "tom.wav"
    run("ffmpeg", "-f", "lavfi", "-i", "sine=f=300:d=3", "-ar", "44100", str(tone))
    beep = d / "tom-bipe.wav"
    subprocess.run(bleep_cmd(tone, [(1, 2)], 3, "44100", beep), check=True, capture_output=True)
    pcm = np.frombuffer(subprocess.run(["ffmpeg", "-v", "error", "-i", str(beep), "-ac", "1", "-f", "s16le", "-"],
                                       capture_output=True, check=True).stdout, np.int16).astype(float)

    def strength(t0, t1, hz):  # força relativa de hz no trecho
        seg = pcm[int(t0 * 44100):int(t1 * 44100)]
        f = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
        return f[int(hz * len(seg) / 44100)] / f.max()
    assert strength(0.2, 0.8, 300) > 0.9 and strength(1.005, 1.995, 1000) > 0.9
    assert strength(1.005, 1.995, 300) < 0.01, "som original vazou no bipe"
    assert abs(media_duration(beep) - 3) < 0.02
    art = d / "capa.mp3"
    run("ffmpeg", "-f", "lavfi", "-i", "sine=d=1", "-f", "lavfi", "-i", "color=red:s=32x32:d=1", "-map", "0", "-map", "1",
        "-frames:v", "1", "-c:v", "mjpeg", "-disposition:v", "attached_pic", str(art))
    assert audio_cover(art) is not None
    subprocess.run(bleep_cmd(art, [(0.2, 0.4)], 1, audio_rate(art), d / "capa-previa.wav"), check=True,
                   capture_output=True)
    keep = d / "capa-bipe.mp3"
    subprocess.run(bleep_cmd(art, [(0.2, 0.4)], 1, audio_rate(art), keep), check=True, capture_output=True)
    assert audio_cover(keep) is not None, "capa perdida"
    subprocess.run(channels_cmd(art, channel_filter(1, 1, 1, "stereo"), d / "capa-canais.wav", 1), check=True,
                   capture_output=True)
    st = d / "estereo.wav"
    run("ffmpeg", "-f", "lavfi", "-i", "sine=f=300:d=2", "-f", "lavfi", "-i", "sine=f=500:d=2", "-filter_complex",
        "[0:a][1:a]join=inputs=2:channel_layout=stereo,volume=0.5", str(st))
    assert audio_channels(st) == 2 and audio_channels(quiet) == 1
    L, R = waveform(st, 10, channels=True)
    assert len(L) == len(R) == 10 and max(L + R) == 1
    for out, n_out in (("mono", 1), ("stereo", 2), ("L", 1)):
        dst = d / f"canais-{out}.wav"
        subprocess.run(channels_cmd(st, channel_filter(2, 1, 0, out), dst), check=True, capture_output=True)
        assert audio_channels(dst) == n_out, (out, audio_channels(dst))
    left_only = loudness(d / "canais-mono.wav")["input_i"]  # R em 0: mono = esquerdo inteiro
    assert abs(left_only - loudness(d / "canais-L.wav")["input_i"]) < 0.5
    dst = d / "canais-up.wav"
    subprocess.run(channels_cmd(quiet, channel_filter(1, 1, 1, "stereo"), dst), check=True, capture_output=True)
    assert audio_channels(dst) == 2
    gaps = d / "pausas.wav"
    run("ffmpeg", "-f", "lavfi", "-i", "sine=f=300:d=4", "-af",
        "volume='if(between(t,0.5,1.5)+between(t,2.2,3.2),0.4,0)':eval=frame", str(gaps))
    a, z = silent_ends(gaps, 4)
    assert abs(a - 0.46) < 0.06 and abs(z - 3.25) < 0.06, (a, z)
    assert silent_ends(quiet, 3) == (0.0, 3)
    cut = audio_out(gaps, "-cortado")
    subprocess.run(audio_trim_cmd(gaps, a, z, cut), check=True, capture_output=True)
    assert abs(media_duration(cut) - (z - a)) < 0.03 and cut.suffix == ".wav", media_duration(cut)
    w = waveform(quiet, 20)
    assert len(w) == 20 and max(w) == 1
    t = frame_start(1.03, 25)
    assert abs(t - 1.0) < 1e-9 and abs(frame_start(1.04, 25) - 1.04) < 1e-9
    png = save_frame(vid, frame_start(1.03, 25), 25)
    want = subprocess.run(["ffmpeg", "-v", "error", "-i", str(vid), "-vf", "select=eq(n\\,25)", "-frames:v", "1",
                           "-f", "image2pipe", "-vcodec", "ppm", "-"], check=True, capture_output=True).stdout
    from PIL import Image as _I, ImageChops
    import io as _io
    got = _I.open(png).convert("RGB")
    assert png.suffix == ".png" and got.size == probe_video(vid)[:2]
    assert not ImageChops.difference(got, _I.open(_io.BytesIO(want)).convert("RGB")).getbbox(), "quadro errado"
    for i in range(len(BG_GRADIENTS)):
        assert make_background(("gradient", i), 30, 20).size == (30, 20)
    print("ok")


def segment(dx, dy, n, inner):
    """Índice da fatia da roda sob (dx, dy) relativo ao centro; None no miolo. Fatia 0 centrada no topo, sentido horário."""
    if n == 0 or math.hypot(dx, dy) < inner:
        return None
    step = math.tau / n
    return int(((math.atan2(dx, -dy) + step / 2) % math.tau) / step) % n


# ---------------- UI ----------------
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango, PangoCairo

MODES = {"convert": ("Converter", "Converter"), "tools": ("Ferramentas", "Ferramentas"), "actions": ("Ações", None)}
KEY_NAMES = {"shift": "Shift", "ctrl": "Ctrl", "alt": "Alt", "super": "Super"}
DEFAULT_KEYS = {"convert": "shift", "tools": "ctrl", "actions": "alt"}
KEYS_FILE = Path(GLib.get_user_config_dir()) / "bergamota" / "keys.json"  # a extensão lê este mesmo arquivo


def load_keys(path=KEYS_FILE):
    """Tecla de cada modo; arquivo ausente ou inválido volta ao padrão."""
    import json
    try:
        keys = json.loads(path.read_text())
        names = [keys[m] for m in DEFAULT_KEYS]
        if set(names) <= set(KEY_NAMES) and len(set(names)) == len(names):
            return {m: keys[m] for m in DEFAULT_KEYS}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return dict(DEFAULT_KEYS)


def save_keys(keys, path=KEYS_FILE):
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(keys))


def assign_key(keys, mode, key):
    """Dá a tecla ao modo; se outro modo a usava, ele fica com a tecla antiga: nunca há duas iguais."""
    out = dict(keys)
    for other, used in keys.items():
        if used == key:
            out[other] = keys[mode]
    out[mode] = key
    return out


def notify(msg):
    subprocess.Popen(["notify-send", "-i", "folder-download", "Bergamota", msg])


COMMANDS_FILE = KEYS_FILE.with_name("commands.json")
COMMAND_ICONS = ("terminal", "play", "star", "bolt", "code", "folder", "archive", "copy", "move", "tag", "pen", "sliders",
                 "pdf", "camera", "speed", "join")


class CustomLabel(str):
    """Rótulo de comando do usuário: a roda desenha diferente e usa o ícone escolhido."""

    def __new__(cls, text, icon):
        self = super().__new__(cls, text)
        self.icon = icon
        return self


def label_icon(label):
    return getattr(label, "icon", None) or MENU_ICONS.get(label)


def load_commands(path=COMMANDS_FILE):
    """[{'name', 'icon', 'command', 'terminal'}]; entrada inválida fica de fora, arquivo ausente vale lista vazia."""
    import json
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    out = []
    for c in data if isinstance(data, list) else []:
        if isinstance(c, dict) and str(c.get("name", "")).strip() and str(c.get("command", "")).strip():
            out.append({"name": str(c["name"]), "command": str(c["command"]), "terminal": bool(c.get("terminal")),
                        "icon": c["icon"] if c.get("icon") in COMMAND_ICONS else "terminal"})
    return out


def save_commands(commands, path=COMMANDS_FILE):
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(commands, ensure_ascii=False, indent=2))


def fill_command(template, f):
    """Troca {arquivo}, {nome} e {pasta} já com aspas de shell: nome de arquivo nunca vira comando.
    Sem nenhum desses, o comando roda como foi escrito."""
    import shlex
    fields = {"{arquivo}": str(f), "{nome}": f.name, "{pasta}": str(f.parent)}
    for k, v in fields.items():
        template = template.replace(k, shlex.quote(v))
    return template


def terminal_argv(folder, argv=()):
    """Terminal novo em folder; o ptyxis ignora --working-directory sem --new-window."""
    term = shutil.which("ptyxis") or shutil.which("gnome-terminal")
    if not term:
        raise RuntimeError("nenhum terminal encontrado (ptyxis ou gnome-terminal)")
    new = ["--new-window"] if term.endswith("ptyxis") else []
    return [term, *new, "--working-directory", str(folder), *(["--", *argv] if argv else [])]


def run_command(command, f):
    """Roda para um arquivo; devolve o erro, ou None. No terminal a janela fica aberta para ler a saída."""
    line = fill_command(command["command"], f)
    if command["terminal"]:
        subprocess.Popen(terminal_argv(f.parent, ["sh", "-c", f'{line}; exec "${{SHELL:-sh}}"']))
        return None
    r = subprocess.run(["sh", "-c", line], cwd=f.parent, capture_output=True, text=True)
    return None if r.returncode == 0 else f"{f.name}: {(r.stderr.strip() or f'saiu com código {r.returncode}')[-200:]}"


def run_custom(command, files):
    def work():
        errs = []
        for f in files:
            try:
                errs.append(run_command(command, f))
            except (OSError, RuntimeError) as ex:
                errs.append(f"{f.name}: {ex}")
        errs = [e for e in errs if e]
        if errs:
            GLib.idle_add(notify, f"{command['name']}: " + "\n".join(errs)[:300])
    threading.Thread(target=work, daemon=True).start()


def transfer(files, folder, move):
    """Copia ou move para folder sem nunca sobrescrever (nome repetido vira 'nome (1)'). Devolve os erros."""
    errs = []
    for f in files:
        try:
            if f.is_dir() and folder.resolve().is_relative_to(f.resolve()):
                raise OSError("a pasta de destino fica dentro dela")
            if move and f.parent.resolve() == folder.resolve():
                continue  # já está lá
            dst = unique_path(folder / f.name)
            if move:
                shutil.move(str(f), str(dst))
            elif f.is_dir():
                shutil.copytree(f, dst, symlinks=True)
            else:
                shutil.copy2(f, dst)
        except (OSError, shutil.Error) as ex:
            errs.append(f"{f.name}: {ex}")
    return errs


def ask_folder(files, move, cancel=None):
    """Pergunta a pasta de destino e transfere numa thread; só avisa se algo falhar."""
    dialog = Gtk.FileDialog(title="Mover para" if move else "Copiar para", accept_label="Mover" if move else "Copiar",
                            initial_folder=Gio.File.new_for_path(str(files[0].parent)))

    def chosen(d, res):
        try:
            folder = Path(d.select_folder_finish(res).get_path())
        except GLib.Error:  # cancelado
            return

        def work():
            errs = transfer(files, folder, move)
            if errs:
                GLib.idle_add(notify, "Erro: " + "\n".join(errs)[:300])
        threading.Thread(target=work, daemon=True).start()
    dialog.select_folder(None, cancel, chosen)


def action_items(files, commands=None):
    p = files[0]
    folder = p if p.is_dir() else p.parent
    return [
        ("Copiar para…", lambda: ask_folder(files, False)),
        ("Mover para…", lambda: ask_folder(files, True)),
        ("Copiar caminho", lambda: Gdk.Display.get_default().get_clipboard().set(
            "\n".join(map(str, files)))),
        ("Terminal aqui", lambda: subprocess.Popen(terminal_argv(folder))),
        *[(CustomLabel(c["name"], c["icon"]), lambda c=c: run_custom(c, files))
          for c in (load_commands() if commands is None else commands)],
    ]


def run_job(files, fn, on_done=lambda: None):
    """Roda fn em cada arquivo numa thread e notifica o resultado."""
    def work():
        done, errs = [], []
        for f in files:
            try:
                out = fn(f)
                if out is not None:  # None = ferramenta que abriu uma janela própria
                    done.append(out)
            except Exception as ex:
                err = getattr(ex, "stderr", b"") or b""
                errs.append(f"{f.name}: {err.decode(errors='ignore')[-200:] or ex}")
        GLib.idle_add(finished, done, errs, on_done)
    threading.Thread(target=work, daemon=True).start()


def finished(done, errs, on_done):
    on_done()
    if done:
        notify(f"Pronto: {', '.join(Path(d).name for d in done)}")
    if errs:
        notify("Erro: " + "\n".join(errs))


def items_for(mode, files):
    """[(rótulo, callback())] de um modo da roda."""
    if mode == "actions":
        return action_items(files)
    section = MODES[mode][1]
    return [(label, lambda fn=fn: run_tool(files, fn))
            for sec, label, fn in options(files[0], same_kind(files)) if sec == section]


def paths(file_list):
    return [Path(f.get_path()) for f in file_list.get_files() if f.get_path()]


class Win(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Bergamota", default_width=240, default_height=240)
        self.label = Gtk.Label(label="🍊\n\nSolte arquivos aqui", justify=Gtk.Justification.CENTER)
        self.label.add_css_class("title-2")
        self.set_child(self.label)
        bar = Gtk.HeaderBar()
        gear = Gtk.Button(icon_name="emblem-system-symbolic", tooltip_text="Ajustes", action_name="app.settings")
        bar.pack_end(gear)
        self.set_titlebar(bar)
        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", lambda _t, v, x, y: self.show_menu(paths(v), x, y) or True)
        self.add_controller(drop)

    def show_menu(self, files, x=None, y=None):
        if not files:
            return
        pop = Gtk.Popover()
        pop.set_parent(self.label)
        if x is not None:
            r = Gdk.Rectangle(); r.x, r.y, r.width, r.height = int(x), int(y), 1, 1
            pop.set_pointing_to(r)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        scroll = Gtk.ScrolledWindow(child=box, propagate_natural_height=True, max_content_height=500,
                                    hscrollbar_policy=Gtk.PolicyType.NEVER)
        pop.set_child(scroll)
        name = files[0].name if len(files) == 1 else f"{len(files)} arquivos"
        box.append(Gtk.Label(label=f"<b>{GLib.markup_escape_text(name)}</b>", use_markup=True))

        def add(section, label, cb):
            if section and section != add.last:
                h = Gtk.Label(label=section, xalign=0, margin_top=6)
                h.add_css_class("dim-label")
                box.append(h)
                add.last = section
            row = Gtk.Box(spacing=8)
            row.append(icon_widget(label_icon(label), 16) if label_icon(label) else Gtk.Box(width_request=16))
            row.append(Gtk.Label(label=label, xalign=0))
            b = Gtk.Button(child=row, has_frame=False)
            b.connect("clicked", lambda _b: (pop.popdown(), cb()))
            box.append(b)
        add.last = None

        for section, label, fn in options(files[0], same_kind(files)):
            add(section, label, lambda fn=fn, label=label: self.convert(files, fn, label))
        for label, cb in action_items(files):
            add("Ações", label, cb)
        pop.popup()

    def convert(self, files, fn, label):
        self.label.set_label(f"🍊\n\n{label}…")
        run_tool(files, fn, lambda: self.label.set_label("🍊\n\nSolte arquivos aqui"))


SIZE, BASE, INNER = 300, 420, 72  # janela; tamanho do desenho (escalado para SIZE); raio do miolo (soltar ali cancela)
WHEEL_K = SIZE / BASE
RING_IN, RING_OUT, DISC = 78, 184, 196
GAP, CORNER = 6, 12
FALLBACK_ACCENT = (1.0, 0.42, 0.0)  # laranja quando o sistema não informa cor de destaque
CSS = b"window.wheel, window.wheel > * { background: transparent; box-shadow: none; }"


def system_colors():
    """(escuro?, rgb do destaque) do tema do GNOME via libadwaita."""
    sm = Adw.StyleManager.get_default()
    accent = FALLBACK_ACCENT
    if sm.get_system_supports_accent_colors():
        c = sm.get_accent_color_rgba()
        accent = (c.red, c.green, c.blue)
    return sm.get_dark(), accent


def palette(dark, accent):
    lum = 0.2126 * accent[0] + 0.7152 * accent[1] + 0.0722 * accent[2]
    on_accent = (0.08, 0.08, 0.1) if lum > 0.6 else (1, 1, 1)
    if dark:
        return dict(disc=(0.15, 0.15, 0.17, 0.55), seg=((0.25, 0.25, 0.28), (0.21, 0.21, 0.24)),
                    edge=(1, 1, 1, 0.06), text=(0.93, 0.93, 0.95), dim=(0.62, 0.62, 0.66),
                    center=(0.12, 0.12, 0.14), pill=(0.24, 0.24, 0.27), accent=accent, on_accent=on_accent)
    return dict(disc=(0.89, 0.89, 0.91, 0.55), seg=((0.99, 0.99, 0.995), (0.94, 0.94, 0.95)),
                edge=(0, 0, 0, 0.06), text=(0.1, 0.1, 0.12), dim=(0.42, 0.42, 0.46),
                center=(1, 1, 1), pill=(0.94, 0.94, 0.95), accent=accent, on_accent=on_accent)


def sector_path(cr, cx, cy, a0, a1):
    """Fatia do anel encolhida por CORNER; preencher + contornar com 2*CORNER arredonda os cantos."""
    ri, ro, off = RING_IN + CORNER, RING_OUT - CORNER, GAP / 2 + CORNER
    cr.new_path()
    cr.arc(cx, cy, ro, a0 + off / ro, a1 - off / ro)
    cr.arc_negative(cx, cy, ri, a1 - off / ri, a0 + off / ri)
    cr.close_path()


def fill_rounded(cr, pattern):
    cr.set_source(pattern)
    cr.set_line_width(2 * CORNER)
    cr.set_line_join(1)  # ROUND
    cr.fill_preserve()
    cr.stroke()


def inner_outline(cr, cx, cy, a0, a1, rgb, width):
    """Contorno por dentro da fatia: desenha a fatia cheia e apaga o miolo, sobrando só a borda."""
    import cairo
    cr.push_group()
    cr.set_source_rgb(*rgb)
    cr.set_line_join(1)
    sector_path(cr, cx, cy, a0, a1)
    cr.set_line_width(2 * CORNER)
    cr.fill_preserve()
    cr.stroke()
    cr.set_operator(cairo.OPERATOR_CLEAR)
    sector_path(cr, cx, cy, a0, a1)
    cr.set_line_width(2 * (CORNER - width))
    cr.fill_preserve()
    cr.stroke()
    cr.set_operator(cairo.OPERATOR_OVER)
    cr.pop_group_to_source()
    cr.paint()


def label_layout(cr, text, size, bold, width=None):
    lay = PangoCairo.create_layout(cr)
    fd = Pango.FontDescription.from_string(f"Sans {'Bold' if bold else ''} {size}")
    lay.set_font_description(fd)
    lay.set_alignment(Pango.Alignment.CENTER)
    if width:
        lay.set_width(int(width * Pango.SCALE))
        lay.set_wrap(Pango.WrapMode.WORD)
    lay.set_text(text, -1)
    return lay


def show_centered(cr, lay, x, y, rgb):
    _ink, log = lay.get_pixel_extents()
    cr.move_to(x - log.width / 2 - log.x, y - log.height / 2)
    cr.set_source_rgb(*rgb)
    PangoCairo.show_layout(cr, lay)


def rounded_rect(cr, x, y, w, h, r):
    cr.new_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
    cr.close_path()


def draw_wheel(cr, size, mode, labels, hover, has_files, dark, accent):
    import cairo
    p = palette(dark, accent)
    cx = cy = size / 2

    # sombra suave + disco de fundo
    sh = cairo.RadialGradient(cx, cy + 6, DISC - 10, cx, cy + 6, size / 2)
    sh.add_color_stop_rgba(0, 0, 0, 0, 0.35 if dark else 0.18)
    sh.add_color_stop_rgba(1, 0, 0, 0, 0)
    cr.arc(cx, cy + 6, size / 2, 0, math.tau)
    cr.arc_negative(cx, cy, DISC, math.tau, 0)  # só por fora: o disco é translúcido e escureceria
    cr.set_source(sh)
    cr.fill()
    cr.arc(cx, cy, DISC, 0, math.tau)
    cr.set_source_rgba(*p["disc"])
    cr.fill_preserve()
    cr.set_source_rgba(*p["edge"])
    cr.set_line_width(1)
    cr.stroke()

    n = len(labels)
    step = math.tau / max(n, 1)
    convert = mode == "convert"
    if not n:  # sem opções: anel inteiro, sem fatias
        cr.new_path()
        cr.arc(cx, cy, RING_OUT, 0, math.tau)
        cr.arc_negative(cx, cy, RING_IN, math.tau, 0)
        g = cairo.LinearGradient(0, cy - RING_OUT, 0, cy + RING_OUT)
        g.add_color_stop_rgb(0, *p["seg"][0])
        g.add_color_stop_rgb(1, *p["seg"][1])
        cr.set_source(g)
        cr.fill()
    for i in range(n):
        a0 = -math.pi / 2 + i * step - step / 2  # fatia 0 centrada no topo
        hot = i == hover and labels
        custom = bool(labels) and isinstance(labels[i], CustomLabel)
        if custom:
            inner_outline(cr, cx, cy, a0, a0 + step, p["accent"] if hot else p["dim"], 4 if hot else 2)
        sector_path(cr, cx, cy, a0, a0 + step)
        if hot:
            g = cairo.LinearGradient(0, cy - RING_OUT, 0, cy + RING_OUT)
            g.add_color_stop_rgb(0, *(min(1, c * 1.12 + 0.04) for c in p["accent"]))
            g.add_color_stop_rgb(1, *p["accent"])
        else:
            g = cairo.LinearGradient(0, cy - RING_OUT, 0, cy + RING_OUT)
            g.add_color_stop_rgb(0, *p["seg"][0])
            g.add_color_stop_rgb(1, *p["seg"][1])
        if custom:  # comando do usuário: só o contorno, nunca preenchido
            cr.new_path()
        else:
            cr.set_line_width(2 * CORNER + 1)
            cr.set_line_join(1)
            cr.set_source_rgba(*p["edge"])
            cr.stroke()
            sector_path(cr, cx, cy, a0, a0 + step)
            fill_rounded(cr, g)
        if labels:
            a = a0 + step / 2
            r = (RING_IN + RING_OUT) / 2
            width = min(2 * r * math.sin(step / 2) - 18, RING_OUT - RING_IN - 14)
            lay = label_layout(cr, labels[i] if convert else labels[i].upper(),
                               15 if convert else 11, True, width)
            x, y = cx + r * math.cos(a), cy + r * math.sin(a)
            color = p["on_accent"] if hot and not custom else p["text"]
            icon = None if convert else label_icon(labels[i])
            if icon:
                _ink, log = lay.get_pixel_extents()
                top = y - (24 + 4 + log.height) / 2
                cr.save()
                cr.translate(x - 12, top)
                cr.scale(1.2, 1.2)
                tool_icon(cr, icon, (*color, 1))
                cr.restore()
                y = top + 28 + log.height / 2
            show_centered(cr, lay, x, y, color)

    # miolo: modo + pílula com a opção atual
    cr.new_path()
    cr.arc(cx, cy, INNER - 4, 0, math.tau)
    cr.set_source_rgb(*p["center"])
    cr.fill_preserve()
    cr.set_source_rgba(*p["edge"])
    cr.set_line_width(1)
    cr.stroke()
    key = KEY_NAMES[load_keys()[mode]]
    show_centered(cr, label_layout(cr, f"{key}  {MODES[mode][0].upper()}", 9, True), cx, cy - 22, p["dim"])
    current = labels[hover] if labels and hover is not None else \
        ("Solte" if labels else ("Nada aqui" if has_files else "Arraste"))
    lay = label_layout(cr, current, 12, True)
    lay.set_width(int((2 * (INNER - 12) - 16) * Pango.SCALE))  # nome longo de comando não vaza da pílula
    lay.set_ellipsize(Pango.EllipsizeMode.END)
    _ink, log = lay.get_pixel_extents()
    pw = min(max(log.width + 24, 64), 2 * (INNER - 12))
    rounded_rect(cr, cx - pw / 2, cy - 5, pw, 30, 15)
    cr.set_source_rgb(*p["pill"])
    cr.fill()
    show_centered(cr, lay, cx, cy + 10, p["text"])


BACKDROP = Path(GLib.get_user_runtime_dir()) / "bergamota-bg.png"  # captura da área sob a roda, feita pela extensão


def load_backdrop():
    """Captura recente da extensão, desfocada, como superfície cairo; None se não houver (o disco fica só translúcido)."""
    from PIL import Image, ImageFilter
    import time
    try:
        fresh = time.time() - BACKDROP.stat().st_mtime < 3
        im = Image.open(BACKDROP).convert("RGB") if fresh else None
        BACKDROP.unlink()
    except OSError:
        return None
    return im and pil_to_surface(im.filter(ImageFilter.GaussianBlur(14 * im.width / SIZE)))


class Wheel(Gtk.ApplicationWindow):
    """Roda radial que a extensão do GNOME Shell abre sob o cursor durante um arrasto."""

    def __init__(self, app):
        super().__init__(application=app, title="Bergamota Wheel", decorated=False, resizable=False,
                         default_width=SIZE, default_height=SIZE)
        self.add_css_class("wheel")
        css = Gtk.CssProvider(); css.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, 800)
        self.mode, self.files, self.items, self.hover, self.dropped = "convert", [], [], None, False
        self.backdrop = None
        self.area = Gtk.DrawingArea(content_width=SIZE, content_height=SIZE)
        self.area.set_draw_func(self.draw)
        self.set_child(self.area)
        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.set_preload(True)
        drop.connect("notify::value", lambda t, _p: t.get_value() and self.set_files(paths(t.get_value())))
        drop.connect("motion", self.on_motion)
        drop.connect("leave", lambda *_: self.set_hover(None))
        drop.connect("drop", self.on_drop)
        self.add_controller(drop)
        sm = Adw.StyleManager.get_default()
        for prop in ("dark", "accent-color"):
            sm.connect(f"notify::{prop}", lambda *_: self.area.queue_draw())

    def open(self, mode):
        if not self.get_visible():  # trocar de modo com a roda aberta mantém o mesmo fundo
            self.backdrop = load_backdrop()
        self.mode, self.dropped = mode, False
        self.refresh()
        self.present()

    def set_files(self, files):
        self.files = files
        self.refresh()

    def refresh(self):
        self.items = items_for(self.mode, self.files) if self.files else []
        if self.hover is not None and self.hover >= len(self.items):  # outro modo pode ter menos fatias
            self.hover = None
        self.area.queue_draw()

    def set_hover(self, i):
        if i != self.hover:
            self.hover = i
            self.area.queue_draw()

    def on_motion(self, _t, x, y):
        self.set_hover(segment((x - SIZE / 2) / WHEEL_K, (y - SIZE / 2) / WHEEL_K, len(self.items), INNER))
        return Gdk.DragAction.COPY

    def on_drop(self, _t, value, x, y):
        self.dropped = True
        self.set_files(paths(value))
        i = segment((x - SIZE / 2) / WHEEL_K, (y - SIZE / 2) / WHEEL_K, len(self.items), INNER)
        if i is not None:
            self.items[i][1]()
        self.close_wheel()
        return True

    def cancel_later(self):
        # O shell avisa o fim do arrasto antes ou depois do drop; espera o drop chegar.
        GLib.timeout_add(200, lambda: self.dropped or self.close_wheel())

    def close_wheel(self):
        self.files, self.items, self.hover = [], [], None
        self.set_visible(False)

    def draw(self, _area, cr, w, _h):
        cr.scale(WHEEL_K, WHEEL_K)  # vetorial: menor e igualmente nítido
        if self.backdrop:  # vidro fosco: o que está atrás, desfocado, por baixo do disco translúcido
            cr.save()
            cr.arc(BASE / 2, BASE / 2, DISC, 0, math.tau)
            cr.clip()
            cr.scale(BASE / self.backdrop.get_width(), BASE / self.backdrop.get_height())
            cr.set_source_surface(self.backdrop, 0, 0)
            cr.paint()
            cr.restore()
        draw_wheel(cr, BASE, self.mode, [l for l, _cb in self.items], self.hover, bool(self.files),
                   *system_colors())


EDITOR_CSS = b"""
.photo-stage { background: #161618; border-radius: 14px; }
.adj-value { font-feature-settings: "tnum"; min-width: 64px; }
"""
PREVIEW_MAX = 900


def to_texture(arr):
    import numpy as np
    h, w = arr.shape[:2]
    data = GLib.Bytes.new((arr * 255 + 0.5).astype(np.uint8).tobytes())
    return Gdk.MemoryTexture.new(w, h, Gdk.MemoryFormat.R8G8B8, data, w * 3)


def load_rgb(src):
    import numpy as np
    from PIL import Image, ImageOps
    im = flatten(ImageOps.exif_transpose(Image.open(src)))
    return np.asarray(im, np.float32) / 255


def fmt_value(fmt, x):
    return fmt.format(x) if x else fmt.format(0).replace("+", "")


def pil_texture(im):
    im = im.convert("RGB")
    return Gdk.MemoryTexture.new(im.width, im.height, Gdk.MemoryFormat.R8G8B8, GLib.Bytes.new(im.tobytes()),
                                 im.width * 3)


def slider_row(label, lo, hi, step, fmt, default, on_change):
    """Linha 'nome | slider | valor | desfazer'; devolve (linha, scale)."""
    name = Gtk.Label(label=label, xalign=0, width_chars=16, max_width_chars=16, ellipsize=3)  # END
    scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, step)
    scale.set_value(default)
    scale.set_draw_value(False)
    scale.set_hexpand(True)
    scale.set_has_origin(lo >= 0)  # bipolar: sem preenchimento a partir da ponta
    value = Gtk.Label(label=fmt_value(fmt, default), xalign=1)
    value.add_css_class("adj-value")
    value.add_css_class("dim-label")
    undo = Gtk.Button(icon_name="edit-undo-symbolic", valign=Gtk.Align.CENTER, sensitive=False,
                      tooltip_text="Redefinir")
    undo.add_css_class("flat")
    undo.add_css_class("circular")
    undo.connect("clicked", lambda _b: scale.set_value(default))

    def changed(sc):
        x = round(sc.get_value() / step) * step
        value.set_label(fmt_value(fmt, x))
        undo.set_sensitive(x != default)
        on_change(x)
    scale.connect("value-changed", changed)
    box = Gtk.Box(spacing=10, margin_start=14, margin_end=6, margin_top=4, margin_bottom=4)
    for wdg in (name, scale, value, undo):
        box.append(wdg)
    dbl = Gtk.GestureClick()
    dbl.connect("pressed", lambda _g, n, *_: n == 2 and scale.set_value(default))
    name.add_controller(dbl)
    return Gtk.ListBoxRow(child=box, activatable=False, selectable=False), scale


def boxed_list(rows):
    box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    box.add_css_class("boxed-list")
    for r in rows:
        box.append(r)
    return box


def tool_header(win, title, subtitle, save_label, on_reset):
    """Cabeçalho comum das ferramentas de foto: Redefinir | título | Salvar."""
    header = Adw.HeaderBar(title_widget=Adw.WindowTitle(title=title, subtitle=subtitle))
    reset = Gtk.Button(icon_name="edit-undo-symbolic", tooltip_text="Redefinir")  # ícone: sobra espaço para o título
    reset.connect("clicked", lambda _b: on_reset())
    header.pack_start(reset)
    win.save_label = save_label
    win.save_btn = Gtk.Button(label=save_label)
    win.save_btn.add_css_class("suggested-action")
    win.save_btn.connect("clicked", lambda _b: win.save())
    header.pack_end(win.save_btn)
    return header


def load_css(*blobs):
    for data in blobs:
        css = Gtk.CssProvider(); css.load_from_data(data)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, 800)


class PhotoTool:
    """Prévia em thread (descarta resultados velhos) e 'salvar cópia' comuns às ferramentas de foto.
    A classe define preview_job() -> callable e show_preview(resultado)."""
    gen, busy, pending = 0, False, False

    def schedule(self):
        self.gen += 1
        if self.busy:
            self.pending = True
            return
        self.busy = True
        gen, job = self.gen, self.preview_job()
        threading.Thread(target=lambda: GLib.idle_add(self._preview_done, gen, job()), daemon=True).start()

    def _preview_done(self, gen, out):
        self.busy = False
        if gen == self.gen:
            self.show_preview(out)
        if self.pending or gen != self.gen:
            self.pending = False
            self.schedule()

    def save_copy(self, suffix, render):
        """render() roda numa thread e devolve uma imagem PIL."""
        e = self.src.suffix.lower().lstrip(".")
        ext = e if e in ("jpg", "jpeg", "png", "webp", "tiff", "tif") else "png"
        self.save_btn.set_sensitive(False)
        self.save_btn.set_label("Salvando…")

        def work():
            try:
                dst = out_path(self.src, ext, suffix)
                render().save(dst, quality=95)
                msg = f"Salvo: {dst.name}"
            except Exception as ex:
                msg = f"Erro ao salvar: {ex}"
            GLib.idle_add(self._saved, msg)
        threading.Thread(target=work, daemon=True).start()

    def save_ffmpeg(self, cmd, dst):
        self.save_btn.set_sensitive(False)
        self.save_btn.set_label("Salvando…")

        def work():
            r = subprocess.run(cmd, capture_output=True, text=True)
            msg = f"Salvo: {dst.name}" if r.returncode == 0 else \
                f"Erro ao salvar: {(r.stderr.strip().splitlines() or ['ffmpeg falhou'])[-1]}"
            GLib.idle_add(self._saved, msg)
        threading.Thread(target=work, daemon=True).start()

    def _saved(self, msg):
        self.save_btn.set_sensitive(True)
        self.save_btn.set_label(self.save_label)
        self.toast.add_toast(Adw.Toast(title=msg, timeout=3))


class VideoPlayer:
    """Player GStreamer (playbin + appsink; o backend de mídia do GTK nem sempre vem instalado) das ferramentas de vídeo.
    A classe define on_frame(l, a, bytes BGRx), on_position(), play_range() -> (início, fim) e self.play_btn."""
    playing, rate = False, 1.0

    def setup_player(self, src):
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
        Gst.init(None)
        reg = Gst.Registry.get()
        for plugin in ("va", "vaapi"):  # VA-API no playbin entrega o quadro em pausa vazio (verde); a prévia vai a 1280 px, CPU dá conta
            for f in reg.get_feature_list_by_plugin(plugin):
                f.set_rank(Gst.Rank.NONE)
        self.Gst, self.pos = Gst, 0.0
        sink_bin = Gst.parse_bin_from_description(
            "videoflip video-direction=auto ! videoconvert ! videoscale ! "
            "video/x-raw,format=BGRx,pixel-aspect-ratio=1/1,width=[2,1280],height=[2,1280] ! "
            "appsink name=sink emit-signals=true max-buffers=1 drop=true", True)
        sink = sink_bin.get_by_name("sink")
        sink.connect("new-sample", self.on_sample, "pull-sample")
        sink.connect("new-preroll", self.on_sample, "pull-preroll")
        self.pipe = Gst.ElementFactory.make("playbin")
        self.pipe.set_property("uri", src.resolve().as_uri())
        self.pipe.set_property("video-sink", sink_bin)
        self.pipe.set_property("audio-filter", Gst.ElementFactory.make("scaletempo"))  # velocidade sem mudar o tom
        bus = self.pipe.get_bus()
        bus.add_signal_watch()
        bus.connect("message::eos", lambda *_: self.pause())
        self.pipe.set_state(Gst.State.PAUSED)
        self.connect("close-request", self.on_close)

    def on_sample(self, sink, action):
        sample = sink.emit(action)
        if sample:
            st = sample.get_caps().get_structure(0)
            buf = sample.get_buffer()
            ok, info = buf.map(self.Gst.MapFlags.READ)
            if ok:
                data = bytes(info.data)
                buf.unmap(info)
                GLib.idle_add(self.on_frame, st.get_value("width"), st.get_value("height"), data)
        return self.Gst.FlowReturn.OK

    def on_close(self, _w):
        self.playing = False
        self.pipe.set_state(self.Gst.State.NULL)
        self.pipe.get_bus().remove_signal_watch()
        return False

    def seek(self, t):
        self.pos = min(max(t, 0), self.dur)
        # +1 ms: cair dentro do quadro, não na fronteira com o anterior
        Gst = self.Gst
        self.pipe.seek(self.rate, Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE, Gst.SeekType.SET,
                       int((self.pos + 0.001) * 1e9), Gst.SeekType.NONE, -1)
        self.on_position()

    def toggle(self):
        self.pause() if self.playing else self.play()

    def play(self):
        start, end = self.play_range()
        if not start <= self.pos < end - 1 / self.fps:
            self.seek(start)
        self.pipe.set_state(self.Gst.State.PLAYING)
        self.playing = True
        self.play_btn.set_icon_name("media-playback-pause-symbolic")
        GLib.timeout_add(40, self.tick)

    def pause(self):
        if self.playing:
            self.pipe.set_state(self.Gst.State.PAUSED)
        self.playing = False
        self.play_btn.set_icon_name("media-playback-start-symbolic")

    def tick(self):
        if not self.playing:
            return False
        ok, ns = self.pipe.query_position(self.Gst.Format.TIME)
        if ok:
            self.pos = ns / 1e9
            end = self.play_range()[1]
            if self.pos >= end:  # toca só o trecho escolhido
                self.pause()
                self.seek(end - 1 / self.fps)
            self.on_position()
        return self.playing

    def step(self, d):
        self.pause()
        self.seek((round(self.pos * self.fps) + d) / self.fps)

    def player_buttons(self, box):
        """⏮ ▶ ⏭ no box; guarda o botão de tocar em self.play_btn."""
        for icon, tip, fn in (("media-seek-backward-symbolic", "Quadro anterior (←)", lambda: self.step(-1)),
                              ("media-playback-start-symbolic", "Tocar (Espaço)", self.toggle),
                              ("media-seek-forward-symbolic", "Próximo quadro (→)", lambda: self.step(1))):
            b = Gtk.Button(icon_name=icon, tooltip_text=tip, valign=Gtk.Align.CENTER)
            b.add_css_class("flat")
            b.add_css_class("circular")
            b.connect("clicked", lambda _b, fn=fn: fn())
            box.append(b)
            if fn == self.toggle:
                self.play_btn = b

    def on_key(self, _c, keyval, _code, _state):
        k = Gdk.keyval_name(keyval)
        if k == "space":
            self.toggle()
        elif k in ("Left", "Right"):
            self.step(-1 if k == "Left" else 1)
        else:
            return False
        return True


def open_editor(src):
    Editor(Gio.Application.get_default(), Path(src)).present()


class Editor(PhotoTool, Adw.ApplicationWindow):
    """Editar foto: prévia ao vivo, ajustes de luz, cor e detalhe, salva uma cópia."""

    def __init__(self, app, src):
        super().__init__(application=app, title=f"Editar foto — {src.name}", default_width=400, default_height=670)
        load_css(EDITOR_CSS)
        from PIL import Image
        import numpy as np
        self.src, self.values, self.scales = src, {}, {}
        self.full = load_rgb(src)
        h, w = self.full.shape[:2]
        k = min(1, PREVIEW_MAX / max(h, w))
        small = Image.fromarray((self.full * 255).astype(np.uint8)).resize((max(1, int(w * k)), max(1, int(h * k))),
                                                                          Image.LANCZOS)
        self.preview_src = np.asarray(small, np.float32) / 255
        self.original_tex = to_texture(self.preview_src)
        header = tool_header(self, "Editar foto", src.name, "Salvar cópia", self.reset_all)

        # prévia: segure o clique para ver o original
        self.picture = Gtk.Picture(paintable=self.original_tex, content_fit=Gtk.ContentFit.CONTAIN,
                                   can_shrink=True, vexpand=True)
        self.picture.set_tooltip_text("Segure o clique para comparar com o original")
        hold = Gtk.GestureClick()
        hold.connect("pressed", lambda *_: self.picture.set_paintable(self.original_tex))
        hold.connect("released", lambda *_: self.picture.set_paintable(self.edited_tex))
        hold.connect("stopped", lambda *_: self.picture.set_paintable(self.edited_tex))
        self.picture.add_controller(hold)
        self.edited_tex = self.original_tex
        stage = Gtk.Box(margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        stage.add_css_class("photo-stage")
        self.picture.set_margin_top(12); self.picture.set_margin_bottom(12)
        self.picture.set_margin_start(12); self.picture.set_margin_end(12)
        self.picture.set_hexpand(True)
        stage.append(self.picture)
        stage.set_size_request(-1, 270)

        # controles
        page = Adw.PreferencesPage()
        for section, rows in ADJUSTMENTS.items():
            group = Adw.PreferencesGroup(title=section)
            lines = []
            for key, label, lo, hi, step, fmt in rows:
                row, self.scales[key] = slider_row(label, lo, hi, step, fmt, 0,
                                                   lambda x, key=key: self.set_value(key, x))
                lines.append(row)
            group.add(boxed_list(lines))
            page.add(group)

        paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL, start_child=stage, end_child=page,
                          resize_start_child=True, shrink_start_child=False, shrink_end_child=False)
        paned.set_position(420)
        view = Adw.ToolbarView(content=paned)
        view.add_top_bar(header)
        self.toast = Adw.ToastOverlay(child=view)
        self.set_content(self.toast)

    def set_value(self, key, x):
        self.values[key] = x
        self.schedule()

    def reset_all(self):
        for sc in self.scales.values():
            sc.set_value(0)

    def preview_job(self):
        vals = dict(self.values)
        return lambda: adjust(self.preview_src, vals)

    def show_preview(self, out):
        self.edited_tex = to_texture(out)
        self.picture.set_paintable(self.edited_tex)

    def save(self):
        from PIL import Image
        import numpy as np
        vals = dict(self.values)
        self.save_copy("-editado", lambda: Image.fromarray((adjust(self.full, vals) * 255 + 0.5).astype(np.uint8)))


# ---------------- Recortar ----------------
CROP_RATIOS = [("Livre", None), ("Original", "orig"), ("1:1", 1), ("16:9", 16 / 9), ("9:16", 9 / 16), ("4:3", 4 / 3),
               ("3:4", 3 / 4)]
CROP_MIN = 8
HANDLES = ("nw", "n", "ne", "e", "se", "s", "sw", "w")
HANDLE_CURSORS = {"nw": "nw-resize", "n": "n-resize", "ne": "ne-resize", "e": "e-resize", "se": "se-resize",
                  "s": "s-resize", "sw": "sw-resize", "w": "w-resize"}


def handle_points(r):
    x0, y0, x1, y1 = r
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return {"nw": (x0, y0), "n": (cx, y0), "ne": (x1, y0), "e": (x1, cy), "se": (x1, y1), "s": (cx, y1),
            "sw": (x0, y1), "w": (x0, cy)}


def ratio_rect(ratio, W, H, r=None):
    """Maior retângulo na proporção, centrado no recorte atual e dentro da imagem."""
    x0, y0, x1, y1 = r or (0, 0, W, H)
    w = min(W, H * ratio)
    h = w / ratio
    cx = min(max((x0 + x1) / 2, w / 2), W - w / 2)
    cy = min(max((y0 + y1) / 2, h / 2), H - h / 2)
    return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


def move_rect(r, dx, dy, W, H):
    x0, y0, x1, y1 = r
    dx = min(max(dx, -x0), W - x1)
    dy = min(max(dy, -y0), H - y1)
    return (x0 + dx, y0 + dy, x1 + dx, y1 + dy)


def drag_handle(r, handle, p, ratio, W, H):
    """Novo recorte ao arrastar uma alça até p (coordenadas da imagem), respeitando proporção e limites."""
    x0, y0, x1, y1 = r
    px, py = min(max(p[0], 0), W), min(max(p[1], 0), H)
    if ratio is None:
        if "w" in handle:
            x0 = min(px, x1 - CROP_MIN)
        if "e" in handle:
            x1 = max(px, x0 + CROP_MIN)
        if "n" in handle:
            y0 = min(py, y1 - CROP_MIN)
        if "s" in handle:
            y1 = max(py, y0 + CROP_MIN)
        return (x0, y0, x1, y1)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    if len(handle) == 2:  # canto: o canto oposto fica parado
        sx, sy = (-1 if "w" in handle else 1), (-1 if "n" in handle else 1)
        ax, ay = (x1 if sx < 0 else x0), (y1 if sy < 0 else y0)
        w = max(CROP_MIN, (px - ax) * sx, (py - ay) * sy * ratio)
        w = min(w, ax if sx < 0 else W - ax, (ay if sy < 0 else H - ay) * ratio)
        h = w / ratio
        (x0, x1), (y0, y1) = sorted((ax, ax + sx * w)), sorted((ay, ay + sy * h))
    elif handle in ("e", "w"):  # lado: o outro eixo cresce centrado
        s = 1 if handle == "e" else -1
        ax = x0 if s > 0 else x1
        w = min(max(CROP_MIN, (px - ax) * s), W - ax if s > 0 else ax, 2 * min(cy, H - cy) * ratio)
        h = w / ratio
        (x0, x1), (y0, y1) = sorted((ax, ax + s * w)), (cy - h / 2, cy + h / 2)
    else:
        s = 1 if handle == "s" else -1
        ay = y0 if s > 0 else y1
        h = min(max(CROP_MIN, (py - ay) * s), H - ay if s > 0 else ay, 2 * min(cx, W - cx) / ratio)
        w = h * ratio
        (y0, y1), (x0, x1) = sorted((ay, ay + s * h)), (cx - w / 2, cx + w / 2)
    return (x0, y0, x1, y1)


def crop_box(r):
    return tuple(int(round(v)) for v in r)


def open_cropper(src):
    Cropper(Gio.Application.get_default(), Path(src)).present()


class Cropper(PhotoTool, Adw.ApplicationWindow):
    """Recortar: alças, proporções prontas e tamanho exato em pixels; salva uma cópia."""

    def __init__(self, app, src):
        super().__init__(application=app, title=f"Recortar — {src.name}", default_width=450, default_height=610)
        load_css(EDITOR_CSS, BG_CSS)
        from PIL import Image
        self.src = src
        self.photo = self.load_photo()
        self.W, self.H = self.photo.size
        k = min(1, 1600 / max(self.photo.size))
        small = flatten(self.photo).resize((max(1, round(self.W * k)), max(1, round(self.H * k))),
                                                 Image.LANCZOS)
        self.preview, self.pk = pil_to_surface(small), self.W / small.width
        self.rect, self.ratio, self.drag, self.syncing = (0, 0, self.W, self.H), None, None, False
        header = tool_header(self, "Recortar", src.name, "Salvar recorte", self.reset_all)

        self.area = Gtk.DrawingArea(hexpand=True, vexpand=True)
        self.area.set_draw_func(self.draw)
        stage = Gtk.Box(margin_top=12, margin_start=12, margin_end=12)
        stage.add_css_class("bg-stage")
        stage.append(self.area)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self.on_begin)
        drag.connect("drag-update", self.on_update)
        drag.connect("drag-end", lambda *_: self.end_drag())
        self.area.add_controller(drag)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", lambda _m, x, y: self.drag or self.area.set_cursor_from_name(self.cursor_at(x, y)))
        self.area.add_controller(motion)

        ratios = Adw.WrapBox(hexpand=True, child_spacing=4, line_spacing=4, justify=Adw.JustifyMode.NONE,
                             align=1)  # quebra a linha em janela estreita
        first, self.ratio_btns = None, []
        for name, r in CROP_RATIOS:
            b = Gtk.ToggleButton(label=name, active=r is None, group=first)
            first = first or b
            b.connect("toggled", lambda b, r=r: b.get_active() and self.set_ratio(r))
            ratios.append(b)
            self.ratio_btns.append(b)
        rrow = Gtk.Box(spacing=10, margin_start=14, margin_end=14, margin_top=8, margin_bottom=8)
        rrow.append(Gtk.Label(label="Proporção", xalign=0))
        rrow.append(ratios)

        self.spin_w = Gtk.SpinButton.new_with_range(CROP_MIN, self.W, 1)
        self.spin_h = Gtk.SpinButton.new_with_range(CROP_MIN, self.H, 1)
        srow = Gtk.Box(spacing=8, margin_start=14, margin_end=14, margin_top=8, margin_bottom=8)
        for label, spin, axis in (("L", self.spin_w, "w"), ("A", self.spin_h, "h")):
            srow.append(Gtk.Label(label=label))
            spin.set_hexpand(True)
            spin.connect("value-changed", lambda sp, axis=axis: self.set_size(axis, sp.get_value()))
            srow.append(spin)
        unit = Gtk.Label(label="px")
        unit.add_css_class("dim-label")
        srow.append(unit)

        rows = [Gtk.ListBoxRow(child=w, activatable=False, selectable=False) for w in (rrow, srow, *self.extra_rows())]
        controls = Adw.Clamp(child=boxed_list(rows), margin_top=12, margin_bottom=12, margin_start=12,
                             margin_end=12, maximum_size=900)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(stage)
        body.append(controls)
        view = Adw.ToolbarView(content=body)
        view.add_top_bar(header)
        self.toast = Adw.ToastOverlay(child=view)
        self.set_content(self.toast)
        self.sync_spins()

    def load_photo(self):
        from PIL import Image, ImageOps
        return ImageOps.exif_transpose(Image.open(self.src))

    def extra_rows(self):
        return []

    # ---- estado ----
    def ratio_value(self, r):
        return self.W / self.H if r == "orig" else r

    def set_ratio(self, r):
        self.ratio = self.ratio_value(r)
        if self.ratio:
            self.set_rect(ratio_rect(self.ratio, self.W, self.H, self.rect))
        self.area.queue_draw()

    def set_rect(self, r):
        self.rect = r
        self.sync_spins()
        self.area.queue_draw()

    def sync_spins(self):
        self.syncing = True
        x0, y0, x1, y1 = crop_box(self.rect)
        self.spin_w.set_value(x1 - x0)
        self.spin_h.set_value(y1 - y0)
        self.syncing = False

    def set_size(self, axis, v):
        """Tamanho digitado: mantém o centro; com proporção, ajusta o outro lado."""
        if self.syncing:
            return
        x0, y0, x1, y1 = self.rect
        w, h = x1 - x0, y1 - y0
        if axis == "w":
            w = v
            h = w / self.ratio if self.ratio else h
        else:
            h = v
            w = h * self.ratio if self.ratio else w
        if w > self.W or h > self.H:  # não cabe: encolhe mantendo a proporção pedida
            f = min(self.W / w, self.H / h)
            w, h = w * f, h * f
        cx = min(max((x0 + x1) / 2, w / 2), self.W - w / 2)
        cy = min(max((y0 + y1) / 2, h / 2), self.H - h / 2)
        self.set_rect((cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2))

    def reset_all(self):
        self.ratio_btns[0].set_active(True)
        self.set_rect((0, 0, self.W, self.H))

    # ---- geometria tela <-> imagem ----
    def view_xform(self):
        W, H = self.area.get_width(), self.area.get_height()
        k = min((W - 48) / self.W, (H - 48) / self.H)
        return k, (W - self.W * k) / 2, (H - self.H * k) / 2

    def to_img(self, x, y):
        k, ox, oy = self.view_xform()
        return ((x - ox) / k, (y - oy) / k)

    def handle_at(self, x, y):
        k, ox, oy = self.view_xform()
        for name, (hx, hy) in handle_points(self.rect).items():
            if abs(ox + hx * k - x) <= 14 and abs(oy + hy * k - y) <= 14:
                return name
        px, py = self.to_img(x, y)
        x0, y0, x1, y1 = self.rect
        return "move" if x0 <= px <= x1 and y0 <= py <= y1 else None

    def cursor_at(self, x, y):
        h = self.handle_at(x, y)
        return HANDLE_CURSORS.get(h, "move" if h == "move" else "default")

    # ---- desenho ----
    def draw(self, _area, cr, w, h):
        import cairo
        k, ox, oy = self.view_xform()
        cr.save()
        cr.translate(ox, oy)
        cr.scale(k, k)
        paint_photo(cr, self.preview, self.pk)
        x0, y0, x1, y1 = self.rect
        cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        cr.rectangle(0, 0, self.W, self.H)
        cr.rectangle(x0, y0, x1 - x0, y1 - y0)
        cr.set_source_rgba(0, 0, 0, 0.55)
        cr.fill()
        cr.restore()
        X0, Y0, X1, Y1 = ox + x0 * k, oy + y0 * k, ox + x1 * k, oy + y1 * k
        if self.drag:  # regra dos terços enquanto arrasta
            cr.set_source_rgba(1, 1, 1, 0.45)
            cr.set_line_width(1)
            for t in (1 / 3, 2 / 3):
                cr.move_to(X0 + (X1 - X0) * t, Y0); cr.line_to(X0 + (X1 - X0) * t, Y1)
                cr.move_to(X0, Y0 + (Y1 - Y0) * t); cr.line_to(X1, Y0 + (Y1 - Y0) * t)
            cr.stroke()
        c = Adw.StyleManager.get_default().get_accent_color_rgba() \
            if Adw.StyleManager.get_default().get_system_supports_accent_colors() else None
        accent = (c.red, c.green, c.blue) if c else FALLBACK_ACCENT
        cr.set_source_rgb(*accent)
        cr.set_line_width(2)
        cr.rectangle(X0, Y0, X1 - X0, Y1 - Y0)
        cr.stroke()
        for name, (hx, hy) in handle_points((X0, Y0, X1, Y1)).items():
            if len(name) == 2:
                rounded_rect(cr, hx - 6, hy - 6, 12, 12, 2.5)
            elif name in ("n", "s"):
                rounded_rect(cr, hx - 12, hy - 3.5, 24, 7, 3.5)
            else:
                rounded_rect(cr, hx - 3.5, hy - 12, 7, 24, 3.5)
            cr.fill()

    # ---- gestos ----
    def on_begin(self, _g, x, y):
        self.drag = self.handle_at(x, y)
        self.start, self.start_rect = (x, y), self.rect
        self.area.queue_draw()

    def on_update(self, _g, dx, dy):
        if not self.drag:
            return
        k = self.view_xform()[0]
        if self.drag == "move":
            self.set_rect(move_rect(self.start_rect, dx / k, dy / k, self.W, self.H))
        else:
            p = self.to_img(self.start[0] + dx, self.start[1] + dy)
            self.set_rect(drag_handle(self.start_rect, self.drag, p, self.ratio, self.W, self.H))

    def end_drag(self):
        self.drag = None
        self.area.queue_draw()

    def save(self):
        box = crop_box(self.rect)
        photo = self.photo
        self.save_copy("-recortado", lambda: photo.crop(box) if photo.mode in ("RGB", "RGBA", "L")
                       else photo.convert("RGB").crop(box))


def open_video_cropper(src):
    VideoCropper(Gio.Application.get_default(), Path(src)).present()


class VideoCropper(VideoPlayer, Cropper):
    """Recortar vídeo: o mesmo recorte das fotos sobre o vídeo tocando; mantém o áudio original."""

    def __init__(self, app, src):
        super().__init__(app, src)
        self.setup_player(src)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)

    def load_photo(self):
        self.fps, self.dur = probe_video(self.src)[2:]
        return video_frame(self.src, 0)  # quadro inteiro: dá o tamanho real (já girado) para o recorte

    def extra_rows(self):
        self.syncing_scrub = False
        self.scrub = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, max(self.dur, 0.01), 0.01)
        self.scrub.set_draw_value(False)
        self.scrub.set_hexpand(True)
        self.scrub.connect("value-changed", lambda sc: self.syncing_scrub or (self.pause(), self.seek(sc.get_value())))
        self.time_label = Gtk.Label(label=f"{fmt_tc(0)} / {fmt_tc(self.dur)}")
        self.time_label.add_css_class("timecode")
        self.time_label.add_css_class("dim-label")
        row = Gtk.Box(spacing=4, margin_start=8, margin_end=14, margin_top=6, margin_bottom=6)
        self.player_buttons(row)
        row.append(self.scrub)
        row.append(self.time_label)
        load_css(TRIM_CSS)
        return [row]

    def on_frame(self, w, h, data):
        import cairo
        self.preview = cairo.ImageSurface.create_for_data(bytearray(data), cairo.FORMAT_RGB24, w, h, w * 4)
        self.pk = self.W / w
        self.area.queue_draw()

    def on_position(self):
        self.syncing_scrub = True
        self.scrub.set_value(self.pos)
        self.syncing_scrub = False
        self.time_label.set_label(f"{fmt_tc(self.pos)} / {fmt_tc(self.dur)}")

    def play_range(self):
        return 0.0, self.dur

    def save(self):
        dst = video_out(self.src, "-recortado")
        self.save_ffmpeg(crop_cmd(self.src, crop_box(self.rect), dst), dst)


# ---------------- Colagem ----------------
COLLAGE_STYLES = [("grid", "Grade"), ("row", "Linha"), ("column", "Coluna"), ("featured", "Destaque")]
COLLAGE_CANVAS = [("Automático", None), ("Quadrado · 1:1", 1), ("Paisagem · 16:9", 16 / 9), ("Paisagem · 4:3", 4 / 3),
                  ("Retrato · 4:5", 4 / 5), ("Story · 9:16", 9 / 16)]
COLLAGE_WIDTHS = [1080, 1600, 2048, 3000, 4096]
COLLAGE_FITS = [("fill", "Preencher (corta)"), ("fit", "Ajustar (inteira)")]
COLLAGE_PREVIEW_W = 720


def collage_grid(style, n, cols):
    """(colunas, linhas) da grade para o estilo."""
    cols = {"row": n, "column": 1}.get(style, max(1, min(cols, n)))
    return cols, -(-n // cols)


def collage_height(style, n, cols, W, ratio, gap, margin, aspect):
    """Altura do quadro: pela proporção escolhida ou, no automático, pela proporção média das imagens."""
    if ratio:
        return round(W / ratio)
    if style == "featured":
        return round(W * 3 / 4)
    c, r = collage_grid(style, n, cols)
    cw = (W - 2 * margin - (c - 1) * gap) / c
    return round(2 * margin + r * cw / aspect + (r - 1) * gap)


def collage_cells(style, n, cols, W, H, gap, margin):
    """Retângulos (x, y, w, h) de cada imagem, na ordem."""
    x0, y0, iw, ih = margin, margin, W - 2 * margin, H - 2 * margin
    if style == "featured" and n > 1:
        rest = n - 1
        if W >= H:  # grande à esquerda, demais empilhadas à direita
            bw = (iw - gap) * 0.62
            sw, sh = iw - gap - bw, (ih - (rest - 1) * gap) / rest
            return [(x0, y0, bw, ih)] + [(x0 + bw + gap, y0 + i * (sh + gap), sw, sh) for i in range(rest)]
        bh = (ih - gap) * 0.62
        sw, sh = (iw - (rest - 1) * gap) / rest, ih - gap - bh
        return [(x0, y0, iw, bh)] + [(x0 + i * (sw + gap), y0 + bh + gap, sw, sh) for i in range(rest)]
    c, r = collage_grid(style, n, cols)
    cw, ch = (iw - (c - 1) * gap) / c, (ih - (r - 1) * gap) / r
    return [(x0 + (i % c) * (cw + gap), y0 + (i // c) * (ch + gap), cw, ch) for i in range(n)]


def render_collage(images, style, cols, W, ratio, fit, gap, margin, corners, bg):
    """images: PIL; medidas em pixels do resultado."""
    import statistics
    from PIL import Image, ImageOps
    aspect = statistics.median(im.width / im.height for im in images)
    H = collage_height(style, len(images), cols, W, ratio, gap, margin, aspect)
    out = Image.new("RGB", (W, max(1, H)), tuple(int(c * 255 + 0.5) for c in bg))
    for im, (x, y, w, h) in zip(images, collage_cells(style, len(images), cols, W, H, gap, margin)):
        x, y, w, h = round(x), round(y), max(1, round(w)), max(1, round(h))
        if fit == "fill":
            tile = ImageOps.fit(im, (w, h), Image.LANCZOS)
        else:
            tile = im.copy()
            tile.thumbnail((w, h), Image.LANCZOS)
            x, y = x + (w - tile.width) // 2, y + (h - tile.height) // 2
        out.paste(tile.convert("RGB"), (x, y), rounded_mask(tile.width, tile.height, round(corners)))
    return out


def open_collage(files):
    Collage(Gio.Application.get_default(), [Path(f) for f in files]).present()


class Collage(PhotoTool, Adw.ApplicationWindow):
    """Colagem: junta várias imagens em grade, linha, coluna ou destaque, num tamanho final escolhido."""

    def __init__(self, app, files):
        super().__init__(application=app, title="Colagem", default_width=430, default_height=640)
        load_css(EDITOR_CSS, BG_CSS)
        from PIL import Image, ImageOps
        self.files = files
        self.src = files[0].parent / "colagem.jpg"  # nome do arquivo salvo
        self.images = [flatten(ImageOps.exif_transpose(Image.open(f))) for f in files]
        self.small = []
        for im in self.images:
            t = im.copy()
            t.thumbnail((900, 900), Image.LANCZOS)
            self.small.append(t)
        self.order = list(range(len(files)))
        self.defaults = dict(style="grid", cols=2 if len(files) < 5 else 3, ratio=1, width=2048, fit="fill",
                             gap=16, margin=16, corners=0, bg=(1, 1, 1))
        self.v = dict(self.defaults)
        header = tool_header(self, "Colagem", f"{len(files)} imagens", "Criar colagem", self.reset_all)

        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, hexpand=True, vexpand=True,
                                   margin_top=16, margin_bottom=16, margin_start=16, margin_end=16)
        stage = Gtk.Box(margin_top=12, margin_bottom=6, margin_start=12, margin_end=12)
        stage.add_css_class("bg-stage")
        stage.append(self.picture)
        stage.set_size_request(-1, 170)

        group = Adw.PreferencesGroup()
        self.style_row = self.combo("Estilo", [n for _k, n in COLLAGE_STYLES],
                                    lambda i: self.set("style", COLLAGE_STYLES[i][0]))
        self.cols_row = Adw.SpinRow(title="Colunas", adjustment=Gtk.Adjustment(
            lower=1, upper=max(1, len(files)), step_increment=1, value=self.v["cols"]))
        self.cols_row.connect("notify::value", lambda r, _p: self.set("cols", int(r.get_value())))
        self.canvas_row = self.combo("Tela", [n for n, _r in COLLAGE_CANVAS],
                                     lambda i: self.set("ratio", COLLAGE_CANVAS[i][1]), 1)
        self.width_row = self.combo("Largura", [f"{w:,} px".replace(",", ".") for w in COLLAGE_WIDTHS],
                                    lambda i: self.set("width", COLLAGE_WIDTHS[i]), COLLAGE_WIDTHS.index(2048))
        self.fit_row = self.combo("Ajuste das imagens", [n for _k, n in COLLAGE_FITS],
                                  lambda i: self.set("fit", COLLAGE_FITS[i][0]))
        for r in (self.style_row, self.cols_row, self.canvas_row, self.width_row, self.fit_row):
            group.add(r)

        spacing = Adw.ExpanderRow(title="Espaçamento e fundo")
        self.scales = {}
        for key, label, hi in (("gap", "Espaçamento", 200), ("margin", "Margem", 300), ("corners", "Cantos", 200)):
            row, self.scales[key] = slider_row(label, 0, hi, 1, "{:.0f} px", self.defaults[key],
                                               lambda x, key=key: self.set(key, x))
            spacing.add_row(row)
        self.color_btn = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False), valign=Gtk.Align.CENTER)
        self.set_color_btn(self.defaults["bg"])
        self.color_btn.connect("notify::rgba", lambda c, _p: self.set("bg", (c.get_rgba().red, c.get_rgba().green,
                                                                            c.get_rgba().blue)))
        crow = Adw.ActionRow(title="Fundo")
        crow.add_suffix(self.color_btn)
        spacing.add_row(crow)
        group.add(spacing)

        self.order_row = Adw.ExpanderRow(title="Ordem das imagens")
        self.order_children = []
        group.add(self.order_row)
        self.fill_order()

        controls = Gtk.ScrolledWindow(child=Adw.Clamp(child=group, margin_top=12, margin_bottom=12, margin_start=12,
                                                       margin_end=12, maximum_size=900),
                                      hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        # divisória arrastável: a prévia não engole os controles
        body = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL, start_child=stage, end_child=controls,
                         position=420, shrink_start_child=False, shrink_end_child=False, wide_handle=True)
        view = Adw.ToolbarView(content=body)
        view.add_top_bar(header)
        self.toast = Adw.ToastOverlay(child=view)
        self.set_content(self.toast)
        self.cols_row.set_sensitive(True)
        self.schedule()

    def combo(self, title, labels, on_pick, selected=0):
        row = Adw.ComboRow(title=title, model=Gtk.StringList.new(labels), selected=selected)
        row.connect("notify::selected", lambda r, _p: on_pick(r.get_selected()))
        return row

    def set_color_btn(self, rgb):
        c = Gdk.RGBA(); c.red, c.green, c.blue, c.alpha = *rgb, 1
        self.color_btn.set_rgba(c)

    # ---- ordem ----
    def fill_order(self):
        for child in self.order_children:
            self.order_row.remove(child)
        self.order_children = []
        for pos, i in enumerate(self.order):
            row = Adw.ActionRow(title=self.files[i].name, title_lines=1)
            thumb = Gtk.Picture(paintable=pil_texture(self.small[i]), content_fit=Gtk.ContentFit.COVER,
                                width_request=40, height_request=40, can_shrink=True)
            row.add_prefix(thumb)
            for icon, d, ok in (("go-up-symbolic", -1, pos > 0), ("go-down-symbolic", 1, pos < len(self.order) - 1)):
                b = Gtk.Button(icon_name=icon, valign=Gtk.Align.CENTER, sensitive=ok)
                b.add_css_class("flat")
                b.connect("clicked", lambda _b, pos=pos, d=d: self.move(pos, d))
                row.add_suffix(b)
            self.order_row.add_row(row)
            self.order_children.append(row)

    def move(self, pos, d):
        o = self.order
        o[pos], o[pos + d] = o[pos + d], o[pos]
        self.fill_order()
        self.schedule()

    # ---- estado ----
    def set(self, key, val):
        self.v[key] = val
        if key == "style":
            self.cols_row.set_sensitive(val == "grid")
        self.schedule()

    def reset_all(self):
        d = self.defaults
        self.style_row.set_selected(0)
        self.cols_row.set_value(d["cols"])
        self.canvas_row.set_selected(1)
        self.width_row.set_selected(COLLAGE_WIDTHS.index(d["width"]))
        self.fit_row.set_selected(0)
        for key, sc in self.scales.items():
            sc.set_value(d[key])
        self.set_color_btn(d["bg"])
        self.order = list(range(len(self.files)))
        self.fill_order()
        self.v = dict(d)
        self.schedule()

    def job(self, images, W):
        """Render com medidas proporcionais à largura pedida (prévia usa largura menor)."""
        v, k = self.v, W / self.v["width"]
        imgs = [images[i] for i in self.order]
        return lambda: render_collage(imgs, v["style"], v["cols"], W, v["ratio"], v["fit"], v["gap"] * k,
                                      v["margin"] * k, v["corners"] * k, v["bg"])

    def preview_job(self):
        return self.job(self.small, COLLAGE_PREVIEW_W)

    def show_preview(self, im):
        self.picture.set_paintable(pil_texture(im))

    def save(self):
        self.save_copy("", self.job(self.images, self.v["width"]))


# ---------------- Fundo ----------------
# Degradês "malha": cor base + manchas (x, y, cor) em coordenadas 0..1 do quadro.
BG_GRADIENTS = [
    ((0.97, 0.55, 0.42), [(0, 0, (0.95, 0.47, 0.62)), (0.9, 0.05, (0.98, 0.85, 0.32)),
                          (0.95, 1, (0.5, 0.36, 0.95)), (0, 0.65, (0.95, 0.4, 0.38))]),
    ((0.98, 0.98, 1), [(0.15, 0, (0.4, 0.58, 0.98)), (0.85, 0.05, (0.98, 0.72, 0.52))]),
    ((0.95, 0.85, 0.45), [(0, 0, (0.98, 0.95, 0.4)), (1, 0.2, (0.95, 0.45, 0.75)), (0.2, 1, (0.45, 0.85, 0.5)),
                          (0.9, 0.9, (0.98, 0.55, 0.3))]),
    ((0.99, 0.97, 0.98), [(0.3, 0, (0.95, 0.72, 0.78)), (0.9, 0.2, (0.85, 0.8, 0.95))]),
    ((0.88, 0.68, 0.88), [(0, 0, (0.78, 0.55, 0.92)), (1, 0.4, (0.98, 0.75, 0.55)), (0.4, 1, (0.92, 0.6, 0.8))]),
    ((0.12, 0.2, 0.3), [(0, 0.2, (0.1, 0.55, 0.5)), (0.8, 0.1, (0.9, 0.3, 0.32)), (0.5, 1, (0.95, 0.75, 0.3))]),
    ((0.6, 0.82, 0.96), [(0, 0, (0.32, 0.5, 0.96)), (1, 1, (0.72, 0.95, 0.82))]),
    ((0.35, 0.75, 0.88), [(0, 1, (0.25, 0.55, 0.85)), (1, 0, (0.55, 0.92, 0.9))]),
    ((0.96, 0.95, 0.99), [(0, 0, (0.8, 0.82, 0.98)), (1, 1, (0.95, 0.85, 0.95))]),
]
BG_SOLIDS = [(0.98, 0.98, 0.97), (0.95, 0.78, 0.33), (0.68, 0.82, 0.32), (0.33, 0.62, 0.42), (0.27, 0.6, 0.56),
             (0.43, 0.78, 0.86), (0.25, 0.37, 0.82), (0.14, 0.18, 0.28), (0.45, 0.3, 0.75)]
BG_RATIOS = [("Auto", None), ("1:1", 1), ("4:5", 4 / 5), ("16:9", 16 / 9), ("9:16", 9 / 16)]
BG_CSS = b"""
.bg-stage { background: alpha(currentColor, 0.06); border-radius: 14px; }
button.swatch { padding: 2px; min-width: 0; min-height: 0; border-radius: 11px; background: none; }
button.swatch:checked { box-shadow: inset 0 0 0 2px @accent_color; }
"""


def gradient_image(spec, w, h):
    """Degradê malha num tamanho pequeno, ampliado (é suave, não perde nada)."""
    import numpy as np
    from PIL import Image
    base, blobs = spec
    gw, gh = max(2, min(w, 160)), max(2, min(h, 160))
    yy, xx = np.mgrid[0:1:complex(gh), 0:1:complex(gw)]
    img = np.empty((gh, gw, 3), np.float32)
    img[:] = base
    for bx, by, color in blobs:
        wgt = np.exp(-((xx - bx) ** 2 + (yy - by) ** 2) / (2 * 0.33 ** 2))[..., None]
        img = img * (1 - wgt) + np.array(color, np.float32) * wgt
    return Image.fromarray((img * 255 + 0.5).astype(np.uint8)).resize((w, h), Image.BICUBIC)


def make_background(bg, w, h):
    """bg: ("gradient", i) | ("solid", rgb) | ("image", PIL) | ("photo", PIL)."""
    from PIL import Image, ImageFilter, ImageOps
    kind, val = bg
    if kind == "gradient":
        return gradient_image(BG_GRADIENTS[val], w, h)
    if kind == "solid":
        return Image.new("RGB", (w, h), tuple(int(c * 255 + 0.5) for c in val))
    im = ImageOps.fit(val.convert("RGB"), (w, h), Image.LANCZOS)
    if kind == "photo":  # a própria foto, bem desfocada
        im = im.filter(ImageFilter.GaussianBlur(max(w, h) * 0.03))
    return im


def canvas_size(pw, ph, pad, ratio):
    w, h = pw + 2 * pad, ph + 2 * pad
    if ratio:
        if w / h < ratio:
            w = h * ratio
        else:
            h = w / ratio
    return round(w), round(h)


def rounded_mask(w, h, r):
    from PIL import Image, ImageDraw
    if r <= 0:
        return None
    ss = 3  # superamostragem para borda suave
    m = Image.new("L", (w * ss, h * ss), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w * ss - 1, h * ss - 1], r * ss, fill=255)
    return m.resize((w, h), Image.LANCZOS)


def render_framed(photo, bg, pad, corners, shadow, blur, ratio):
    """Foto (PIL) centralizada num quadro novo. Medidas em pixels da própria foto."""
    from PIL import Image, ImageDraw, ImageFilter
    pw, ph = photo.size
    W, H = canvas_size(pw, ph, pad, ratio)
    out = make_background(bg, W, H)
    if blur:
        out = out.filter(ImageFilter.GaussianBlur(blur / 100 * 0.04 * max(W, H)))
    x, y = (W - pw) // 2, (H - ph) // 2
    if shadow:
        m = Image.new("L", (W, H), 0)
        dy = shadow * 0.35
        ImageDraw.Draw(m).rounded_rectangle([x, y + dy, x + pw, y + ph + dy], corners, fill=140)
        out = Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), out, m.filter(ImageFilter.GaussianBlur(shadow)))
    out.paste(photo.convert("RGB"), (x, y), rounded_mask(pw, ph, round(corners)))
    return out


def open_background(src):
    Background(Gio.Application.get_default(), Path(src)).present()


class Background(PhotoTool, Adw.ApplicationWindow):
    """Fundo: coloca a foto num quadro com degradê, cor ou imagem, cantos, sombra e proporção."""

    def __init__(self, app, src):
        super().__init__(application=app, title=f"Fundo — {src.name}", default_width=450, default_height=650)
        load_css(EDITOR_CSS, BG_CSS)
        from PIL import Image, ImageOps
        self.src = src
        self.photo = flatten(ImageOps.exif_transpose(Image.open(src)))
        M = max(self.photo.size)
        self.k = min(1, PREVIEW_MAX / M)
        self.small = self.photo.resize((max(1, round(self.photo.width * self.k)),
                                        max(1, round(self.photo.height * self.k))), Image.LANCZOS)
        # (chave, rótulo, máximo, padrão, formato)
        self.specs = [("pad", "Margem", round(M * 0.5), round(M * 0.1), "{:.0f} px"),
                      ("corners", "Cantos", round(min(self.photo.size) * 0.2), round(M * 0.025), "{:.0f} px"),
                      ("shadow", "Sombra", round(M * 0.08), round(M * 0.02), "{:.0f} px"),
                      ("blur", "Desfoque do fundo", 100, 0, "{:.0f}%")]
        self.values = {k: d for k, _l, _m, d, _f in self.specs}
        self.bg, self.ratio = ("gradient", 0), None
        header = tool_header(self, "Fundo", src.name, "Salvar com fundo", self.reset_all)

        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, hexpand=True, vexpand=True,
                                   margin_top=16, margin_bottom=16, margin_start=16, margin_end=16)
        stage = Gtk.Box(margin_top=12, margin_start=12, margin_end=12)
        stage.add_css_class("bg-stage")
        stage.append(self.picture)
        stage.set_size_request(-1, 260)

        # amostras: foto desfocada + degradês + imagem / cores + cor livre
        grid = Gtk.Grid(column_spacing=6, row_spacing=6)
        self.hidden = Gtk.ToggleButton()  # grupo sem nada marcado (cor livre)
        self.swatches = {}
        specs = [("photo", self.photo)] + [("gradient", i) for i in range(len(BG_GRADIENTS))]
        for col, spec in enumerate(specs):
            grid.attach(self.swatch(spec, "Foto desfocada" if spec[0] == "photo" else None), col, 0, 1, 1)
        for col, rgb in enumerate(BG_SOLIDS):
            grid.attach(self.swatch(("solid", rgb)), col, 1, 1, 1)
        self.image_btn = Gtk.ToggleButton(child=icon_widget("image"), tooltip_text="Usar outra imagem como fundo",
                                          group=self.hidden, valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        self.image_btn.connect("clicked", lambda _b: self.pick_image())
        grid.attach(self.image_btn, len(specs), 0, 1, 1)
        self.color_btn = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False), valign=Gtk.Align.CENTER)
        self.color_btn.set_tooltip_text("Outra cor")
        self.color_btn.connect("notify::rgba", lambda c, _p: self.custom_color(c.get_rgba()))
        grid.attach(self.color_btn, len(BG_SOLIDS), 1, 2, 1)
        self.swatches[("gradient", 0)].set_active(True)
        bg_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_start=14, margin_end=14,
                         margin_top=10, margin_bottom=12)
        bg_row.append(Gtk.Label(label="Fundo", xalign=0))
        grid.set_halign(Gtk.Align.START)
        bg_row.append(grid)

        self.scales, rows = {}, [Gtk.ListBoxRow(child=bg_row, activatable=False, selectable=False)]
        for key, label, hi, default, fmt in self.specs:
            row, self.scales[key] = slider_row(label, 0, hi, 1, fmt, default, lambda x, key=key: self.set_value(key, x))
            rows.append(row)

        ratio_box = Gtk.Box(spacing=0, halign=Gtk.Align.END, hexpand=True)
        ratio_box.add_css_class("linked")
        first, self.ratio_btns = None, []
        for name, r in BG_RATIOS:
            b = Gtk.ToggleButton(label=name, active=r is None, group=first)
            first = first or b
            b.connect("toggled", lambda b, r=r: b.get_active() and self.set_ratio(r))
            ratio_box.append(b)
            self.ratio_btns.append(b)
        rbox = Gtk.Box(spacing=10, margin_start=14, margin_end=14, margin_top=8, margin_bottom=8)
        rbox.append(Gtk.Label(label="Proporção", xalign=0))
        rbox.append(ratio_box)
        rows.append(Gtk.ListBoxRow(child=rbox, activatable=False, selectable=False))

        controls = Gtk.ScrolledWindow(child=Adw.Clamp(child=boxed_list(rows), margin_top=12, margin_bottom=12,
                                                       margin_start=12, margin_end=12, maximum_size=900),
                                      hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(stage)
        body.append(controls)
        view = Adw.ToolbarView(content=body)
        view.add_top_bar(header)
        self.toast = Adw.ToastOverlay(child=view)
        self.set_content(self.toast)
        self.schedule()

    def swatch(self, spec, tip=None):
        thumb = pil_to_surface(make_background(("photo", self.small) if spec[0] == "photo" else spec, 72, 72))
        area = Gtk.DrawingArea(content_width=26, content_height=26)

        def draw(_a, cr, w, h):
            rounded_rect(cr, 0.5, 0.5, w - 1, h - 1, 9)
            cr.save()
            cr.clip_preserve()
            cr.scale(w / 72, h / 72)
            cr.set_source_surface(thumb, 0, 0)
            cr.paint()
            cr.restore()
            cr.set_source_rgba(0, 0, 0, 0.12)
            cr.set_line_width(1)
            cr.stroke()
        area.set_draw_func(draw)
        b = Gtk.ToggleButton(child=area, group=self.hidden, tooltip_text=tip)
        b.add_css_class("swatch")
        b.connect("toggled", lambda b: b.get_active() and self.set_bg(spec))
        self.swatches[spec if spec[0] != "photo" else ("photo",)] = b
        return b

    # ---- estado ----
    def set_bg(self, spec):
        self.bg = spec
        self.schedule()

    def set_value(self, key, x):
        self.values[key] = x
        self.schedule()

    def set_ratio(self, r):
        self.ratio = r
        self.schedule()

    def custom_color(self, rgba):
        self.hidden.set_active(True)
        self.set_bg(("solid", (rgba.red, rgba.green, rgba.blue)))

    def pick_image(self):
        f = Gtk.FileFilter(name="Imagens")
        f.add_pixbuf_formats()
        dialog = Gtk.FileDialog(title="Imagem de fundo", default_filter=f)
        previous = self.bg

        def chosen(d, res):
            try:
                from PIL import Image
                self.set_bg(("image", Image.open(d.open_finish(res).get_path()).convert("RGB")))
                self.image_btn.set_active(True)
            except GLib.Error:
                self.restore_toggle(previous)
        dialog.open(self, None, chosen)

    def restore_toggle(self, spec):
        key = ("photo",) if spec[0] == "photo" else spec
        btn = self.swatches.get(key) if spec[0] != "image" else self.image_btn
        (btn or self.hidden).set_active(True)

    def reset_all(self):
        for key, _l, _m, default, _f in self.specs:
            self.scales[key].set_value(default)
        self.ratio_btns[0].set_active(True)
        self.swatches[("gradient", 0)].set_active(True)

    def params(self, k):
        v = self.values
        bg = ("photo", self.small if k != 1 else self.photo) if self.bg[0] == "photo" else self.bg
        return dict(bg=bg, pad=v["pad"] * k, corners=v["corners"] * k, shadow=v["shadow"] * k, blur=v["blur"],
                    ratio=self.ratio)

    def preview_job(self):
        p = self.params(self.k)
        return lambda: render_framed(self.small, **p)

    def show_preview(self, im):
        self.picture.set_paintable(pil_texture(im))

    def save(self):
        p = self.params(1)
        self.save_copy("-fundo", lambda: render_framed(self.photo, **p))


# ---------------- Anotar foto ----------------
# (id, nome, dica)
ANNOTATE_TOOLS = [
    ("arrow", "Seta", "Arraste da cauda até a ponta da seta."),
    ("marker", "Marca-texto", "Pinte por cima para destacar."),
    ("text", "Texto", "Clique onde o texto deve começar e digite."),
    ("rect", "Retângulo", "Arraste para desenhar um retângulo."),
    ("ellipse", "Elipse", "Arraste para desenhar uma elipse."),
    ("pen", "Caneta", "Desenhe à mão livre."),
    ("number", "Número", "Clique para adicionar etiquetas numeradas em sequência."),
    ("magnifier", "Lupa", "Arraste do centro para fora para ampliar um detalhe."),
    ("image", "Imagem", "Escolha uma imagem para inserir."),
]
ANNOTATE_HINT = " Arraste uma anotação para movê-la."
DEFAULT_INK = (0.93, 0.22, 0.2)
NUMBER_R = 5.5  # raio da etiqueta numerada, em larguras de traço
ANNOTATE_CSS = b"""
button.tool:checked { background: @accent_bg_color; color: @accent_fg_color; }
.color-pill { min-width: 52px; }
"""


def pil_to_surface(im):
    """PIL -> cairo ARGB32 (pré-multiplicado)."""
    import cairo
    import numpy as np
    a = np.asarray(im.convert("RGBA")).astype(np.uint16)
    al = a[..., 3]
    bgra = np.dstack([a[..., 2] * al // 255, a[..., 1] * al // 255, a[..., 0] * al // 255, al]).astype(np.uint8)
    h, w = al.shape
    return cairo.ImageSurface.create_for_data(bytearray(bgra.tobytes()), cairo.FORMAT_ARGB32, w, h, w * 4)


def surface_to_pil(surf):
    import numpy as np
    from PIL import Image
    surf.flush()
    w, h, stride = surf.get_width(), surf.get_height(), surf.get_stride()
    a = np.ndarray((h, stride // 4, 4), np.uint8, buffer=surf.get_data())[:, :w]
    return Image.fromarray(a[..., [2, 1, 0]].copy())


def paint_photo(cr, photo, k):
    import cairo
    cr.save()
    cr.scale(k, k)
    cr.set_source_surface(photo, 0, 0)
    cr.get_source().set_filter(cairo.FILTER_GOOD)
    cr.paint()
    cr.restore()


def text_layout(cr, it):
    lay = PangoCairo.create_layout(cr)
    fd = Pango.FontDescription.from_string("Sans Bold")
    fd.set_absolute_size(it["size"] * Pango.SCALE)
    lay.set_font_description(fd)
    lay.set_text(it["text"], -1)
    return lay


def item_bbox(cr, it):
    """(x0, y0, x1, y1) em coordenadas da imagem."""
    xs, ys = [p[0] for p in it["pts"]], [p[1] for p in it["pts"]]
    x, y, w = xs[0], ys[0], it["w"]
    if it["kind"] == "text":
        _ink, log = text_layout(cr, it).get_pixel_extents()
        return x, y, x + log.width, y + log.height
    if it["kind"] == "number":
        r = w * NUMBER_R
        return x - r, y - r, x + r, y + r
    if it["kind"] == "magnifier":
        r = math.dist(*it["pts"])
        return x - r, y - r, x + r, y + r
    if it["kind"] == "image":
        return x, y, x + it["size"][0], y + it["size"][1]
    m = w * (3 if it["kind"] == "marker" else 2)
    return min(xs) - m, min(ys) - m, max(xs) + m, max(ys) + m


def seg_dist(p, a, b):
    ax, ay, bx, by = *a, *b
    dx, dy = bx - ax, by - ay
    t = 0 if dx == dy == 0 else max(0, min(1, ((p[0] - ax) * dx + (p[1] - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(p[0] - ax - t * dx, p[1] - ay - t * dy)


def hit(cr, it, p, tol):
    if it["kind"] in ("arrow", "pen", "marker"):
        pts = it["pts"] if len(it["pts"]) > 1 else it["pts"] * 2
        reach = it["w"] * (3 if it["kind"] == "marker" else 1.5) + tol
        return any(seg_dist(p, a, b) <= reach for a, b in zip(pts, pts[1:]))
    x0, y0, x1, y1 = item_bbox(cr, it)
    return x0 - tol <= p[0] <= x1 + tol and y0 - tol <= p[1] <= y1 + tol


def draw_item(cr, it, photo, k):
    """Desenha uma anotação em coordenadas da imagem; photo/k servem à lupa."""
    cr.new_path()  # o texto deixa um ponto corrente que ligaria ao próximo arco
    cr.save()
    cr.set_source_rgb(*it["color"])
    cr.set_line_cap(1)   # ROUND
    cr.set_line_join(1)  # ROUND
    w, pts, kind = it["w"], it["pts"], it["kind"]
    if kind == "arrow":
        (x0, y0), (x1, y1) = pts
        ang, head = math.atan2(y1 - y0, x1 - x0), w * 4.5
        cr.set_line_width(w)
        cr.move_to(x0, y0)
        cr.line_to(x1 - math.cos(ang) * head * 0.7, y1 - math.sin(ang) * head * 0.7)
        cr.stroke()
        cr.move_to(x1, y1)
        for da in (2.7, -2.7):
            cr.line_to(x1 + math.cos(ang + da) * head, y1 + math.sin(ang + da) * head)
        cr.close_path()
        cr.fill()
    elif kind in ("pen", "marker"):
        if kind == "marker":
            cr.set_source_rgba(*it["color"], 0.4)
            cr.set_line_cap(2)  # SQUARE
        cr.set_line_width(w * (6 if kind == "marker" else 1))
        cr.move_to(*pts[0])
        for q in pts[1:] or pts:
            cr.line_to(*q)
        cr.stroke()
    elif kind == "rect":
        (x0, y0), (x1, y1) = pts
        rounded_rect(cr, min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0), min(w * 1.5, abs(x1 - x0) / 2,
                                                                                    abs(y1 - y0) / 2))
        cr.set_line_width(w)
        cr.stroke()
    elif kind == "ellipse":
        (x0, y0), (x1, y1) = pts
        cr.save()
        cr.translate((x0 + x1) / 2, (y0 + y1) / 2)
        cr.scale(max(abs(x1 - x0) / 2, 0.5), max(abs(y1 - y0) / 2, 0.5))
        cr.arc(0, 0, 1, 0, math.tau)
        cr.restore()
        cr.set_line_width(w)
        cr.stroke()
    elif kind == "text":
        lay = text_layout(cr, it)
        cr.move_to(*pts[0])
        PangoCairo.layout_path(cr, lay)
        cr.set_source_rgb(1, 1, 1)
        cr.set_line_width(it["size"] * 0.16)
        cr.stroke_preserve()
        cr.set_source_rgb(*it["color"])
        cr.fill()
    elif kind == "number":
        (x, y), r = pts[0], w * NUMBER_R
        cr.arc(x, y, r, 0, math.tau)
        cr.fill_preserve()
        cr.set_source_rgb(1, 1, 1)
        cr.set_line_width(w * 0.7)
        cr.stroke()
        lay = text_layout(cr, {"text": str(it["n"]), "size": r * 1.15})
        _ink, log = lay.get_pixel_extents()
        cr.move_to(x - log.width / 2, y - log.height / 2)
        PangoCairo.show_layout(cr, lay)
    elif kind == "magnifier":
        (cx, cy), r = pts[0], max(math.dist(*pts), 1)
        cr.save()
        cr.arc(cx, cy, r, 0, math.tau)
        cr.clip()
        cr.translate(cx, cy)
        cr.scale(2, 2)
        cr.translate(-cx, -cy)
        paint_photo(cr, photo, k)
        cr.restore()
        cr.arc(cx, cy, r, 0, math.tau)
        cr.set_source_rgb(1, 1, 1)
        cr.set_line_width(w * 2.2)
        cr.stroke_preserve()
        cr.set_source_rgb(*it["color"])
        cr.set_line_width(w)
        cr.stroke()
    elif kind == "image":
        (x, y), (iw, ih) = pts[0], it["size"]
        surf = it["img"]
        cr.translate(x, y)
        cr.scale(iw / surf.get_width(), ih / surf.get_height())
        cr.set_source_surface(surf, 0, 0)
        cr.paint()
    cr.restore()


def render_annotated(photo, items):
    """Aplica as anotações sobre a foto em resolução cheia."""
    import cairo
    out = cairo.ImageSurface(cairo.FORMAT_ARGB32, photo.get_width(), photo.get_height())
    cr = cairo.Context(out)
    paint_photo(cr, photo, 1)
    for it in items:
        draw_item(cr, it, photo, 1)
    return out


# Ícone de cada ferramenta/ação na roda e no menu.
MENU_ICONS = {"Comprimir": "compress", "Reduzir 50%": "resize", "Editar": "sliders", "Anotar": "pen",
              "Fundo": "frame", "Recortar": "crop", "Colagem": "collage", "Cortar": "trim", "Velocidade": "speed", "Quadros": "camera", "Ocultar": "redact", "Normalizar volume": "loudness", "Visualizador": "visualizer", "Canais": "channels", "Bipe": "bleep", "Metadados": "tag", "Unir vídeos": "join", "Unir PDFs": "join", "Organizar": "organize", "Dividir": "split", "Compactar ZIP": "archive", "Extrair": "extract", "Copiar caminho": "copypath", "Copiar para…": "copy", "Mover para…": "move",
              "Terminal aqui": "terminal"}


def icon_widget(kind, size=20):
    # centralizado: se esticar junto com o botão, o desenho escala e estoura
    icon = Gtk.DrawingArea(content_width=size, content_height=size, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)

    def draw(a, cr, w, _h):
        c = a.get_color()
        cr.scale(w / 20, w / 20)
        tool_icon(cr, kind, (c.red, c.green, c.blue, c.alpha))
    icon.set_draw_func(draw)
    return icon


def tool_icon(cr, kind, rgba):
    """Ícones próprios em 20x20, independentes do tema de ícones."""
    cr.set_source_rgba(*rgba)
    cr.set_line_width(1.6)
    cr.set_line_cap(1)
    cr.set_line_join(1)
    if kind == "arrow":
        cr.move_to(15, 15); cr.line_to(5, 5); cr.stroke()
        cr.move_to(5, 5); cr.line_to(11, 5.5); cr.line_to(5.5, 11); cr.close_path(); cr.fill()
    elif kind == "marker":
        cr.move_to(7, 13); cr.line_to(12, 4); cr.line_to(15, 6); cr.line_to(10, 15); cr.close_path(); cr.stroke()
        cr.move_to(4, 17); cr.line_to(16, 17); cr.stroke()
    elif kind == "text":
        lay = text_layout(cr, {"text": "Aa", "size": 12})
        _ink, log = lay.get_pixel_extents()
        cr.move_to(10 - log.width / 2, 10 - log.height / 2)
        PangoCairo.show_layout(cr, lay)
    elif kind == "rect":
        rounded_rect(cr, 3, 5, 14, 10, 2); cr.stroke()
    elif kind == "ellipse":
        cr.arc(10, 10, 6.5, 0, math.tau); cr.stroke()
    elif kind == "pen":
        cr.move_to(5, 14); cr.line_to(13, 4); cr.line_to(16, 6.5); cr.line_to(8, 16); cr.line_to(4.5, 16.5)
        cr.close_path(); cr.stroke()
    elif kind == "number":
        cr.arc(10, 10, 7, 0, math.tau); cr.stroke()
        lay = text_layout(cr, {"text": "1", "size": 9})
        _ink, log = lay.get_pixel_extents()
        cr.move_to(10 - log.width / 2, 10 - log.height / 2)
        PangoCairo.show_layout(cr, lay)
    elif kind == "magnifier":
        cr.arc(8.5, 8.5, 5.5, 0, math.tau); cr.stroke()
        cr.move_to(12.5, 12.5); cr.line_to(17, 17); cr.stroke()
        cr.move_to(6, 8.5); cr.line_to(11, 8.5); cr.move_to(8.5, 6); cr.line_to(8.5, 11); cr.stroke()
    elif kind == "image":
        rounded_rect(cr, 2.5, 4, 15, 12, 2); cr.stroke()
        cr.move_to(4, 14.5); cr.line_to(8, 10); cr.line_to(11, 13); cr.line_to(13, 11); cr.line_to(16, 14.5); cr.stroke()
        cr.arc(13, 7.5, 1.3, 0, math.tau); cr.fill()
    elif kind == "collage":
        rounded_rect(cr, 2.5, 2.5, 8, 15, 1.5); cr.stroke()
        rounded_rect(cr, 12.5, 2.5, 5, 6.5, 1.5); cr.stroke()
        rounded_rect(cr, 12.5, 11, 5, 6.5, 1.5); cr.stroke()
    elif kind == "join":
        rounded_rect(cr, 2, 5.5, 7, 9, 1.5); cr.stroke()
        rounded_rect(cr, 11, 5.5, 7, 9, 1.5); cr.stroke()
        cr.move_to(7.5, 10); cr.line_to(12.5, 10); cr.stroke()
    elif kind == "channels":
        for y, hgt in ((6.5, (4, 9, 6, 11, 5)), (13.5, (6, 10, 4, 8, 7))):
            for i, v in enumerate(hgt):
                cr.move_to(4 + i * 3, y - v / 4); cr.line_to(4 + i * 3, y + v / 4); cr.stroke()
    elif kind == "bleep":
        cr.move_to(2.5, 10); cr.line_to(6, 10); cr.stroke()
        rounded_rect(cr, 6, 5, 8, 10, 1.5); cr.fill()
        cr.move_to(14, 10); cr.line_to(17.5, 10); cr.stroke()
    elif kind == "pdf":
        cr.move_to(4.5, 2.5); cr.line_to(12, 2.5); cr.line_to(15.5, 6); cr.line_to(15.5, 17.5); cr.line_to(4.5, 17.5)
        cr.close_path(); cr.stroke()
        for y in (9, 12, 15):
            cr.move_to(7.5, y); cr.line_to(12.5, y); cr.stroke()
    elif kind == "organize":
        for x, y in ((3, 3), (11, 3), (3, 11), (11, 11)):
            rounded_rect(cr, x, y, 6, 6, 1.2); cr.stroke()
    elif kind == "tag":
        cr.move_to(2.5, 3.5); cr.line_to(10, 3.5); cr.line_to(17.5, 11); cr.line_to(10.5, 17.5); cr.line_to(2.5, 10)
        cr.close_path(); cr.stroke()
        cr.arc(6.5, 7.5, 1.3, 0, math.tau); cr.fill()
    elif kind == "split":
        rounded_rect(cr, 3, 2.5, 14, 6, 1.5); cr.stroke()
        rounded_rect(cr, 3, 11.5, 14, 6, 1.5); cr.stroke()
        cr.save(); cr.set_dash([1.5, 2]); cr.move_to(1, 10); cr.line_to(19, 10); cr.stroke(); cr.restore()
    elif kind == "copy":
        rounded_rect(cr, 7, 7, 10.5, 10.5, 2); cr.stroke()
        cr.move_to(13, 3); cr.line_to(4.5, 3); cr.line_to(3, 4.5); cr.line_to(3, 13); cr.stroke()
    elif kind == "move":
        cr.move_to(2.5, 15.5); cr.line_to(2.5, 5); cr.line_to(7.5, 5); cr.line_to(9, 7); cr.line_to(17.5, 7)
        cr.line_to(17.5, 15.5); cr.close_path(); cr.stroke()
        cr.move_to(6.5, 11.5); cr.line_to(13, 11.5); cr.stroke()
        cr.move_to(10.5, 9.2); cr.line_to(13.2, 11.5); cr.line_to(10.5, 13.8); cr.stroke()
    elif kind == "play":
        cr.move_to(6, 3.5); cr.line_to(16, 10); cr.line_to(6, 16.5); cr.close_path(); cr.stroke()
    elif kind == "star":
        for i in range(10):
            r, ang = (8 if i % 2 == 0 else 3.4), -math.pi / 2 + i * math.pi / 5
            (cr.line_to if i else cr.move_to)(10 + r * math.cos(ang), 10.5 + r * math.sin(ang))
        cr.close_path(); cr.stroke()
    elif kind == "bolt":
        cr.move_to(11.5, 2.5); cr.line_to(4.5, 11.5); cr.line_to(9.5, 11.5); cr.line_to(8.5, 17.5)
        cr.line_to(15.5, 8.5); cr.line_to(10.5, 8.5); cr.close_path(); cr.stroke()
    elif kind == "code":
        cr.move_to(7, 5.5); cr.line_to(2.5, 10); cr.line_to(7, 14.5); cr.stroke()
        cr.move_to(13, 5.5); cr.line_to(17.5, 10); cr.line_to(13, 14.5); cr.stroke()
        cr.move_to(11.5, 4); cr.line_to(8.5, 16); cr.stroke()
    elif kind == "visualizer":
        rounded_rect(cr, 2.5, 3.5, 15, 13, 2); cr.stroke()
        for x, hgt in ((6.5, 3), (10, 6), (13.5, 4)):
            cr.move_to(x, 10 - hgt / 2); cr.line_to(x, 10 + hgt / 2); cr.stroke()
    elif kind == "loudness":
        for x, hgt in ((4, 5), (8, 11), (12, 14), (16, 8)):
            cr.move_to(x, 10 - hgt / 2); cr.line_to(x, 10 + hgt / 2); cr.stroke()
    elif kind == "redact":
        rounded_rect(cr, 2.5, 3.5, 15, 13, 2); cr.stroke()
        rounded_rect(cr, 5.5, 7.5, 9, 5, 1); cr.fill()
    elif kind == "camera":
        rounded_rect(cr, 2.5, 5.5, 15, 11, 2); cr.stroke()
        cr.move_to(7, 5.5); cr.line_to(8, 3.5); cr.line_to(12, 3.5); cr.line_to(13, 5.5); cr.stroke()
        cr.arc(10, 11, 3, 0, math.tau); cr.stroke()
    elif kind == "speed":
        cr.arc(10, 12, 7.5, math.pi, 2 * math.pi); cr.stroke()
        cr.move_to(10, 12); cr.line_to(14.5, 7); cr.stroke()
        cr.arc(10, 12, 1.6, 0, math.tau); cr.fill()
    elif kind == "trim":
        for y in (5, 15):
            cr.arc(5, y, 2.5, 0, math.tau); cr.stroke()
        cr.move_to(7, 6.5); cr.line_to(17.5, 14); cr.move_to(7, 13.5); cr.line_to(17.5, 6); cr.stroke()
    elif kind == "crop":
        cr.move_to(6, 2.5); cr.line_to(6, 14); cr.line_to(17.5, 14); cr.stroke()
        cr.move_to(2.5, 6); cr.line_to(14, 6); cr.line_to(14, 17.5); cr.stroke()
    elif kind == "compress":
        for sx, sy in ((1, 1), (-1, -1)):
            ex, ey = 10 + sx * 1.5, 10 + sy * 1.5
            cr.move_to(10 + sx * 7, 10 + sy * 7); cr.line_to(ex, ey); cr.stroke()
            cr.move_to(ex, ey); cr.line_to(ex + sx * 4.5, ey); cr.line_to(ex, ey + sy * 4.5); cr.close_path(); cr.fill()
    elif kind == "resize":
        rounded_rect(cr, 3, 3, 14, 14, 2); cr.stroke()
        rounded_rect(cr, 3, 10, 7, 7, 1.5); cr.fill()
        cr.move_to(12, 8); cr.line_to(14.5, 5.5); cr.stroke()
    elif kind == "sliders":
        for y, x in ((5, 13), (10, 7), (15, 11)):
            cr.move_to(3, y); cr.line_to(17, y); cr.stroke()
            cr.arc(x, y, 2.2, 0, math.tau); cr.fill()
    elif kind == "frame":
        rounded_rect(cr, 2.5, 2.5, 15, 15, 3); cr.stroke()
        rounded_rect(cr, 6, 6, 8, 8, 1.5); cr.fill()
    elif kind == "archive":
        rounded_rect(cr, 3, 4, 14, 4, 1); cr.stroke()
        rounded_rect(cr, 4, 8, 12, 9, 1.5); cr.stroke()
        cr.move_to(8, 11); cr.line_to(12, 11); cr.stroke()
    elif kind == "extract":
        rounded_rect(cr, 3, 9, 14, 8, 1.5); cr.stroke()
        cr.move_to(10, 12); cr.line_to(10, 3); cr.stroke()
        cr.move_to(6.5, 6); cr.line_to(10, 2.5); cr.line_to(13.5, 6); cr.stroke()
    elif kind == "copypath":
        rounded_rect(cr, 4, 2.5, 12, 15, 2); cr.stroke()
        for y in (7, 10, 13):
            cr.move_to(7, y); cr.line_to(13, y); cr.stroke()
    elif kind == "folder":
        cr.move_to(2.5, 15.5); cr.line_to(2.5, 5); cr.line_to(7.5, 5); cr.line_to(9, 7); cr.line_to(17.5, 7)
        cr.line_to(17.5, 15.5); cr.close_path(); cr.stroke()
    elif kind == "terminal":
        rounded_rect(cr, 2.5, 3.5, 15, 13, 2); cr.stroke()
        cr.move_to(5.5, 8); cr.line_to(8, 10); cr.line_to(5.5, 12); cr.stroke()
        cr.move_to(10, 12.5); cr.line_to(14, 12.5); cr.stroke()


def open_annotator(src):
    Annotator(Gio.Application.get_default(), Path(src)).present()


class Annotator(PhotoTool, Adw.ApplicationWindow):
    """Anotar foto: setas, texto, formas, marca-texto, números, lupa e imagens; salva uma cópia."""

    def __init__(self, app, src):
        super().__init__(application=app, title=f"Anotar foto — {src.name}", default_width=540, default_height=640)
        load_css(EDITOR_CSS, ANNOTATE_CSS)
        from PIL import Image, ImageOps
        self.src = src
        im = flatten(ImageOps.exif_transpose(Image.open(src)))
        self.fw, self.fh = im.size
        self.photo = pil_to_surface(im)
        k = min(1, 1600 / max(im.size))
        self.preview = pil_to_surface(im.resize((max(1, int(self.fw * k)), max(1, int(self.fh * k))), Image.LANCZOS))
        self.pk = self.fw / self.preview.get_width()
        self.base = max(self.fw, self.fh) / 250
        self.items, self.history, self.selected = [], [], None
        self.tool, self.ink, self.drag = "arrow", DEFAULT_INK, None

        header = tool_header(self, "Anotar foto", src.name, "Salvar cópia anotada",
                             lambda: self.items and (self.push(), self.set_items([])))

        self.area = Gtk.DrawingArea(hexpand=True, vexpand=True, focusable=True)
        self.area.set_draw_func(self.draw)
        stage = Gtk.Box(margin_top=12, margin_start=12, margin_end=12)
        stage.add_css_class("photo-stage")
        stage.append(self.area)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self.on_begin)
        drag.connect("drag-update", self.on_update)
        drag.connect("drag-end", self.on_end)
        self.area.add_controller(drag)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)

        tools = Gtk.Box(spacing=6, homogeneous=True, margin_top=12, margin_start=12, margin_end=12)
        group = None
        for tid, name, _hint in ANNOTATE_TOOLS:
            b = Gtk.ToggleButton(child=icon_widget(tid), tooltip_text=name, active=tid == "arrow")
            b.add_css_class("tool")
            if group:
                b.set_group(group)
            group = group or b
            b.connect("toggled", lambda b, t=tid: b.get_active() and self.set_tool(t))
            tools.append(b)

        color = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False), valign=Gtk.Align.CENTER)
        rgba = Gdk.RGBA(); rgba.red, rgba.green, rgba.blue, rgba.alpha = *DEFAULT_INK, 1
        color.set_rgba(rgba)
        color.add_css_class("color-pill")
        color.connect("notify::rgba", lambda c, _p: self.set_ink(c.get_rgba()))
        self.undo_btn = Gtk.Button(icon_name="edit-undo-symbolic", tooltip_text="Desfazer (Ctrl+Z)", sensitive=False)
        self.undo_btn.connect("clicked", lambda _b: self.undo())
        self.del_btn = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Apagar anotação (Delete)",
                                  sensitive=False)
        self.del_btn.connect("clicked", lambda _b: self.delete_selected())
        row = Gtk.Box(spacing=6, margin_top=8, margin_start=12, margin_end=12)
        row.append(color)
        row.append(Gtk.Box(hexpand=True))
        row.append(self.undo_btn)
        row.append(self.del_btn)

        self.hint = Gtk.Label(xalign=0, wrap=True, margin_top=10, margin_bottom=12, margin_start=14, margin_end=14)
        self.hint.add_css_class("dim-label")

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for wdg in (stage, tools, row, self.hint):
            body.append(wdg)
        view = Adw.ToolbarView(content=body)
        view.add_top_bar(header)
        self.toast = Adw.ToastOverlay(child=view)
        self.set_content(self.toast)
        self.set_tool("arrow")

    # ---- estado ----
    def set_tool(self, t):
        self.tool = t
        self.hint.set_label(dict((i, h) for i, _n, h in ANNOTATE_TOOLS)[t] + ANNOTATE_HINT)
        if t == "image":
            self.pick_image()

    def set_ink(self, rgba):
        self.ink = (rgba.red, rgba.green, rgba.blue)
        if self.selected is not None:
            self.push()
            self.items[self.selected]["color"] = self.ink
            self.area.queue_draw()

    def snapshot_items(self):
        return [dict(it, pts=list(it["pts"])) for it in self.items]

    def push(self):
        self.history.append((self.snapshot_items(), self.selected))
        self.undo_btn.set_sensitive(True)

    def undo(self):
        if self.history:
            items, sel = self.history.pop()
            self.set_items(items, sel)
            self.undo_btn.set_sensitive(bool(self.history))

    def set_items(self, items, selected=None):
        self.items, self.selected = items, selected
        self.del_btn.set_sensitive(selected is not None)
        self.area.queue_draw()

    def delete_selected(self):
        if self.selected is not None:
            self.push()
            self.set_items([it for i, it in enumerate(self.items) if i != self.selected])

    def new_item(self, kind, pts, **extra):
        return dict(kind=kind, pts=pts, color=self.ink, w=self.base, **extra)

    # ---- geometria tela <-> imagem ----
    def view_xform(self):
        W, H = self.area.get_width(), self.area.get_height()
        k = min((W - 24) / self.fw, (H - 24) / self.fh)
        return k, (W - self.fw * k) / 2, (H - self.fh * k) / 2

    def to_img(self, x, y):
        k, ox, oy = self.view_xform()
        return ((x - ox) / k, (y - oy) / k)

    def measure_cr(self):
        import cairo
        return cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1))

    # ---- desenho ----
    def draw(self, _area, cr, _w, _h):
        k, ox, oy = self.view_xform()
        cr.translate(ox, oy)
        cr.scale(k, k)
        cr.rectangle(0, 0, self.fw, self.fh)
        cr.clip()
        paint_photo(cr, self.preview, self.pk)
        for it in self.items:
            draw_item(cr, it, self.preview, self.pk)
        if self.selected is not None:
            x0, y0, x1, y1 = item_bbox(cr, self.items[self.selected])
            pad = 4 / k
            rounded_rect(cr, x0 - pad, y0 - pad, x1 - x0 + 2 * pad, y1 - y0 + 2 * pad, 4 / k)
            c = Adw.StyleManager.get_default().get_accent_color_rgba()
            cr.set_source_rgba(c.red, c.green, c.blue, 0.95)
            cr.set_line_width(1.5 / k)
            cr.set_dash([5 / k, 4 / k])
            cr.stroke()

    # ---- gestos ----
    def on_begin(self, gesture, x, y):
        self.area.grab_focus()
        p = self.to_img(x, y)
        k = self.view_xform()[0]
        cr = self.measure_cr()
        hit_i = next((i for i in reversed(range(len(self.items))) if hit(cr, self.items[i], p, 6 / k)), None)
        self.start, self.last = (x, y), p
        if hit_i is not None:
            self.push()
            self.drag = "move"
            self.set_items(self.items, hit_i)
            return
        self.set_items(self.items, None)
        if self.tool == "text":
            self.ask_text(x, y, p)
        elif self.tool == "number":
            self.push()
            n = 1 + sum(it["kind"] == "number" for it in self.items)
            self.set_items(self.items + [self.new_item("number", [p], n=n)], len(self.items))
        elif self.tool in ("arrow", "rect", "ellipse", "magnifier", "pen", "marker"):
            self.push()
            pts = [p] if self.tool in ("pen", "marker") else [p, p]
            self.items.append(self.new_item(self.tool, pts))
            self.drag = "draw"
        else:
            self.drag = None

    def on_update(self, _g, dx, dy):
        p = self.to_img(self.start[0] + dx, self.start[1] + dy)
        if self.drag == "move":
            it = self.items[self.selected]
            ddx, ddy = p[0] - self.last[0], p[1] - self.last[1]
            it["pts"] = [(a + ddx, b + ddy) for a, b in it["pts"]]
        elif self.drag == "draw":
            it = self.items[-1]
            if it["kind"] in ("pen", "marker"):
                it["pts"].append(p)
            else:
                it["pts"][1] = p
        self.last = p
        self.area.queue_draw()

    def on_end(self, _g, dx, dy):
        if self.drag == "draw":
            it = self.items[-1]
            if math.hypot(dx, dy) < 4 and it["kind"] != "pen":
                self.items.pop()
                self.history.pop()
                self.undo_btn.set_sensitive(bool(self.history))
            else:
                self.set_items(self.items, len(self.items) - 1)
        elif self.drag == "move" and math.hypot(dx, dy) < 1:
            self.history.pop()  # só selecionou
            self.undo_btn.set_sensitive(bool(self.history))
        self.drag = None
        self.area.queue_draw()

    def on_key(self, _c, keyval, _code, state):
        if keyval in (Gdk.KEY_Delete, Gdk.KEY_BackSpace) and not isinstance(self.get_focus(), Gtk.Text):
            self.delete_selected()
        elif keyval == Gdk.KEY_z and state & Gdk.ModifierType.CONTROL_MASK:
            self.undo()
        elif keyval == Gdk.KEY_Escape:
            self.set_items(self.items, None)
        else:
            return False
        return True

    def ask_text(self, x, y, p):
        pop = Gtk.Popover()
        pop.set_parent(self.area)
        r = Gdk.Rectangle(); r.x, r.y, r.width, r.height = int(x), int(y), 1, 1
        pop.set_pointing_to(r)
        entry = Gtk.Entry(placeholder_text="Digite e tecle Enter", width_chars=24)
        pop.set_child(entry)

        def done(_e):
            text = entry.get_text().strip()
            if text:
                self.push()
                self.set_items(self.items + [self.new_item("text", [p], text=text, size=self.base * 7)],
                               len(self.items))
            pop.popdown()
        entry.connect("activate", done)
        pop.connect("closed", lambda pp: GLib.idle_add(pp.unparent))
        pop.popup()

    def pick_image(self):
        f = Gtk.FileFilter(name="Imagens")
        f.add_pixbuf_formats()
        dialog = Gtk.FileDialog(title="Inserir imagem", default_filter=f)

        def chosen(d, res):
            try:
                gf = d.open_finish(res)
            except GLib.Error:
                return
            from PIL import Image
            im = Image.open(gf.get_path())
            iw = self.fw * 0.35
            ih = iw * im.height / im.width
            self.push()
            it = self.new_item("image", [((self.fw - iw) / 2, (self.fh - ih) / 2)], img=pil_to_surface(im),
                               size=(iw, ih))
            self.set_items(self.items + [it], len(self.items))
        dialog.open(self, None, chosen)

    def save(self):
        items = self.snapshot_items()
        self.save_copy("-anotado", lambda: surface_to_pil(render_annotated(self.photo, items)))


# ---------------- Cortar vídeo ----------------
TRIM_CSS = b"""
.timecode { font-family: monospace; font-feature-settings: "tnum"; }
"""
STRIP_H, STRIP_THUMBS, HANDLE_W = 64, 12, 14


def paint_strip(cr, strip, x0, x1, y, h):
    """Tira de quadros (PNG em mosaico 1xN) esticada de x0 a x1, cada quadro centrado na sua célula."""
    tw, th = strip.get_width() / STRIP_THUMBS, strip.get_height()
    cw, k = (x1 - x0) / STRIP_THUMBS, h / th
    for i in range(STRIP_THUMBS):
        cr.save()
        cr.rectangle(x0 + i * cw, y, cw, h)
        cr.clip()
        cr.translate(x0 + i * cw + (cw - tw * k) / 2 - i * tw * k, y)
        cr.scale(k, k)
        cr.set_source_surface(strip, 0, 0)
        cr.paint()
        cr.restore()


def open_trimmer(src):
    Trimmer(Gio.Application.get_default(), Path(src)).present()


class Trimmer(VideoPlayer, PhotoTool, Adw.ApplicationWindow):
    """Cortar vídeo: arraste as pontas, avance quadro a quadro ou digite os tempos; salva o trecho num novo arquivo."""

    def __init__(self, app, src):
        super().__init__(application=app, title=f"Cortar vídeo — {src.name}", default_width=540, default_height=580)
        load_css(EDITOR_CSS, TRIM_CSS)
        self.src = src
        self.vw, self.vh, self.fps, self.dur = probe_video(src)
        self.start, self.end, self.pos = 0.0, self.dur, 0.0
        self.drag, self.strip = None, None
        header = tool_header(self, "Cortar vídeo", src.name, "Salvar trecho", self.reset_all)

        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, hexpand=True, vexpand=True)
        stage = Gtk.Box(margin_top=12, margin_start=12, margin_end=12)
        stage.add_css_class("photo-stage")
        stage.set_overflow(Gtk.Overflow.HIDDEN)
        stage.append(self.picture)

        hint = Gtk.Label(label="Arraste as pontas para cortar", xalign=0, hexpand=True)
        hint.add_css_class("dim-label")
        self.sel_label = Gtk.Label()
        self.sel_label.add_css_class("dim-label")
        info = Gtk.Box(margin_top=14, margin_start=14, margin_end=14)
        info.append(hint)
        info.append(self.sel_label)

        self.area = Gtk.DrawingArea(height_request=STRIP_H, margin_top=8, margin_start=12, margin_end=12)
        self.area.set_draw_func(self.draw)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self.on_begin)
        drag.connect("drag-update", lambda _g, dx, _dy: self.drag_to(self.drag_x + dx))
        drag.connect("drag-end", lambda *_: setattr(self, "drag", None))
        self.area.add_controller(drag)

        tc = Gtk.Box(spacing=8, margin_top=10, margin_start=14, margin_end=14)
        self.entries = {}
        for which, label in (("start", "Início"), ("end", "Fim")):
            if which == "end":
                tc.append(Gtk.Box(hexpand=True))
            name = Gtk.Label(label=label)
            name.add_css_class("dim-label")
            entry = Gtk.Entry(width_chars=9, max_width_chars=9, xalign=0.5, tooltip_text="m:ss.cc")
            entry.add_css_class("timecode")
            entry.connect("activate", lambda e, w=which: self.commit(e, w))
            leave = Gtk.EventControllerFocus()
            leave.connect("leave", lambda _c, e=entry, w=which: self.commit(e, w))
            entry.add_controller(leave)
            self.entries[which] = entry
            tc.append(name)
            tc.append(entry)

        controls = Gtk.Box(spacing=4, margin_top=10, margin_bottom=14, margin_start=10, margin_end=14)
        self.player_buttons(controls)
        self.pos_label = Gtk.Label(hexpand=True, xalign=1)
        self.pos_label.add_css_class("timecode")
        self.pos_label.add_css_class("dim-label")
        controls.append(self.pos_label)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for wdg in (stage, info, self.area, tc, controls):
            body.append(wdg)
        view = Adw.ToolbarView(content=body)
        view.add_top_bar(header)
        self.toast = Adw.ToastOverlay(child=view)
        self.set_content(self.toast)

        self.setup_player(src)
        threading.Thread(target=self.load_strip, daemon=True).start()
        self.refresh()

    # ---- vídeo ----
    def on_frame(self, w, h, data):
        self.picture.set_paintable(Gdk.MemoryTexture.new(w, h, Gdk.MemoryFormat.B8G8R8X8, GLib.Bytes.new(data), w * 4))

    def on_position(self):
        self.refresh()

    def play_range(self):
        return self.start, self.end

    def load_strip(self):
        from PIL import Image
        import io
        png = subprocess.run(["ffmpeg", "-v", "error", "-i", str(self.src), "-vf",
                              f"fps={STRIP_THUMBS / max(self.dur, 0.01):.6f},scale=-2:{STRIP_H * 2},"
                              f"tile={STRIP_THUMBS}x1", "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
                             capture_output=True).stdout
        if png:
            surf = pil_to_surface(Image.open(io.BytesIO(png)))
            GLib.idle_add(lambda: (setattr(self, "strip", surf), self.area.queue_draw()) and False)

    # ---- trecho ----
    def set_range(self, which, t):
        t = min(max(round(t * self.fps) / self.fps, 0), self.dur)
        if which == "start":
            self.start = min(t, self.end - 1 / self.fps)
            self.seek(self.start)
        else:
            self.end = max(t, self.start + 1 / self.fps)
            self.seek(self.end - 1 / self.fps)  # último quadro mantido

    def commit(self, entry, which):
        t = parse_tc(entry.get_text())
        if t is not None and abs(t - getattr(self, which)) > 1e-3:
            self.set_range(which, t)
        self.refresh()

    def reset_all(self):
        self.pause()
        self.start, self.end = 0.0, self.dur
        self.seek(0)

    def refresh(self):
        self.sel_label.set_label(f"{fmt_tc(self.end - self.start)} selecionados")
        self.pos_label.set_label(f"{fmt_tc(self.pos)} / {fmt_tc(self.dur)}")
        for which, e in self.entries.items():
            e.set_text(fmt_tc(getattr(self, which)))
        self.area.queue_draw()

    # ---- tira de quadros ----
    def track(self):
        return HANDLE_W, self.area.get_width() - HANDLE_W

    def tx(self, t):
        x0, x1 = self.track()
        return x0 + (x1 - x0) * t / self.dur

    def on_begin(self, _g, x, _y):
        self.pause()
        xs, xe = self.tx(self.start), self.tx(self.end)
        self.drag = ("start" if abs(x - (xs - HANDLE_W / 2)) <= HANDLE_W else
                     "end" if abs(x - (xe + HANDLE_W / 2)) <= HANDLE_W else "seek")
        self.drag_x = x
        self.drag_to(x)

    def drag_to(self, x):
        x0, x1 = self.track()
        shift = {"start": HANDLE_W / 2, "end": -HANDLE_W / 2}.get(self.drag, 0)  # x é o meio da alça
        t = (x + shift - x0) / max(1, x1 - x0) * self.dur
        if self.drag == "seek":
            self.seek(min(max(t, self.start), self.end - 1 / self.fps))
        elif self.drag:
            self.set_range(self.drag, t)

    def draw(self, _a, cr, w, h):
        _dark, accent = system_colors()
        x0, x1 = self.track()
        rounded_rect(cr, 0, 0, w, h, 8)
        cr.clip()
        cr.set_source_rgb(0.09, 0.09, 0.1)
        cr.paint()
        self.paint_track(cr, x0, x1, h)
        xs, xe = self.tx(self.start), self.tx(self.end)
        cr.set_source_rgba(0, 0, 0, 0.6)
        cr.rectangle(0, 0, xs, h)
        cr.rectangle(xe, 0, w - xe, h)
        cr.fill()
        cr.set_source_rgb(*accent)
        cr.rectangle(xs, 0, xe - xs, 3)
        cr.rectangle(xs, h - 3, xe - xs, 3)
        cr.fill()
        for hx in (xs - HANDLE_W, xe):
            rounded_rect(cr, hx, 0, HANDLE_W, h, 5)
            cr.fill()
        cr.set_source_rgb(1, 1, 1)
        cr.set_line_width(2)
        cr.set_line_cap(1)
        for gx in (xs - HANDLE_W / 2, xe + HANDLE_W / 2):
            cr.move_to(gx, h * 0.32); cr.line_to(gx, h * 0.68); cr.stroke()
        px = self.tx(self.pos)
        cr.set_source_rgba(0, 0, 0, 0.35)
        cr.rectangle(px - 2, 0, 4, h)
        cr.fill()
        cr.set_source_rgb(1, 1, 1)
        cr.rectangle(px - 1, 0, 2, h)
        cr.fill()

    def paint_track(self, cr, x0, x1, h):
        if self.strip:
            paint_strip(cr, self.strip, x0, x1, 0, h)

    def save(self):
        dst = video_out(self.src, "-cortado")
        self.pause()
        self.save_ffmpeg(trim_cmd(self.src, self.start, self.end, dst), dst)


# ---------------- Ocultar no vídeo ----------------
REDACT_ROW, REDACT_GAP, REDACT_GUTTER, REDACT_STRIP = 34, 8, 30, 52


def open_redactor(src):
    Redactor(Gio.Application.get_default(), Path(src)).present()


class Redactor(VideoPlayer, PhotoTool, Adw.ApplicationWindow):
    """Ocultar no vídeo: caixas de cor sólida ou desfoque, cada uma com seu próprio intervalo de tempo."""

    def __init__(self, app, src):
        super().__init__(application=app, title=f"Ocultar — {src.name}", default_width=480, default_height=650)
        load_css(EDITOR_CSS, TRIM_CSS, BG_CSS, ANNOTATE_CSS)
        self.src = src
        self.W, self.H, self.fps, self.dur = probe_video(src)
        self.boxes = []  # [x0, y0, x1, y1, início, fim]; coordenadas do vídeo já girado
        self.sel, self.preview, self.pk, self.strip = None, None, 1, None
        self.style, self.color = "solid", (0, 0, 0)
        self.drag = self.tdrag = None
        header = tool_header(self, "Ocultar", src.name, "Salvar cópia", self.reset_all)

        self.stage = Gtk.DrawingArea(hexpand=True, vexpand=True)
        self.stage.set_draw_func(self.draw_stage)
        frame = Gtk.Box(margin_top=12, margin_start=12, margin_end=12)
        frame.add_css_class("bg-stage")
        frame.append(self.stage)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self.stage_begin)
        drag.connect("drag-update", self.stage_update)
        drag.connect("drag-end", lambda *_: self.stage_end())
        self.stage.add_controller(drag)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", lambda _m, x, y: self.drag or self.stage.set_cursor_from_name(self.cursor_at(x, y)))
        self.stage.add_controller(motion)

        styles = Gtk.Box(halign=Gtk.Align.END, hexpand=True)
        styles.add_css_class("linked")
        first = None
        for key, name in (("solid", "Sólido"), ("blur", "Desfoque")):
            b = Gtk.ToggleButton(label=name, active=key == "solid", group=first)
            first = first or b
            b.connect("toggled", lambda b, key=key: b.get_active() and self.set_style(key))
            styles.append(b)
        srow = Gtk.Box(spacing=10, margin_start=14, margin_end=14, margin_top=8, margin_bottom=8)
        srow.append(Gtk.Label(label="Estilo", xalign=0))
        srow.append(styles)
        self.color_btn = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False), valign=Gtk.Align.CENTER,
                                               halign=Gtk.Align.END, hexpand=True)
        rgba = Gdk.RGBA(); rgba.red = rgba.green = rgba.blue = 0; rgba.alpha = 1
        self.color_btn.set_rgba(rgba)
        self.color_btn.add_css_class("color-pill")
        self.color_btn.connect("notify::rgba", lambda c, _p: self.set_color(c.get_rgba()))
        self.crow = Gtk.Box(spacing=10, margin_start=14, margin_end=14, margin_top=8, margin_bottom=8)
        self.crow.append(Gtk.Label(label="Cor", xalign=0))
        self.crow.append(self.color_btn)
        settings = Adw.Clamp(child=boxed_list([Gtk.ListBoxRow(child=w, activatable=False, selectable=False)
                                               for w in (srow, self.crow)]),
                             margin_top=12, margin_start=12, margin_end=12, maximum_size=900)

        title = Gtk.Label(label="Ocultações", xalign=0, hexpand=True)
        title.add_css_class("heading")
        self.del_btn = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Remover caixa (Delete)",
                                  sensitive=False)
        self.del_btn.add_css_class("flat")
        self.del_btn.connect("clicked", lambda _b: self.delete_selected())
        trow = Gtk.Box(margin_top=12, margin_start=14, margin_end=10)
        trow.append(title)
        trow.append(self.del_btn)
        self.area = Gtk.DrawingArea(margin_top=6, margin_start=12, margin_end=12)
        self.area.set_draw_func(self.draw_timeline)
        tdrag = Gtk.GestureDrag()
        tdrag.connect("drag-begin", self.time_begin)
        tdrag.connect("drag-update", lambda _g, dx, _dy: self.time_update(dx))
        tdrag.connect("drag-end", lambda _g, dx, _dy: self.time_end(dx))
        self.area.add_controller(tdrag)

        controls = Gtk.Box(spacing=4, margin_top=6, margin_bottom=12, margin_start=10, margin_end=14)
        self.player_buttons(controls)
        self.pos_label = Gtk.Label(hexpand=True, xalign=1)
        self.pos_label.add_css_class("timecode")
        self.pos_label.add_css_class("dim-label")
        controls.append(self.pos_label)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for wdg in (frame, settings, trow, self.area, controls):
            body.append(wdg)
        view = Adw.ToolbarView(content=body)
        view.add_top_bar(header)
        self.toast = Adw.ToastOverlay(child=view)
        self.set_content(self.toast)
        self.setup_player(src)
        threading.Thread(target=Trimmer.load_strip, args=(self,), daemon=True).start()
        self.refresh()

    # ---- estado ----
    def set_style(self, key):
        self.style = key
        self.crow.set_sensitive(key == "solid")
        self.stage.queue_draw()

    def set_color(self, rgba):
        self.color = (rgba.red, rgba.green, rgba.blue)
        self.refresh()

    def select(self, i):
        self.sel = i
        self.refresh()

    def add_box(self, rect, t0, t1):
        self.boxes.append([*rect, t0, t1])
        self.select(len(self.boxes) - 1)

    def default_rect(self):
        w, h = self.W * 0.3, self.H * 0.2
        return (self.W / 2 - w / 2, self.H / 2 - h / 2, self.W / 2 + w / 2, self.H / 2 + h / 2)

    def delete_selected(self):
        if self.sel is not None:
            del self.boxes[self.sel]
            self.sel = None
            self.refresh()

    def reset_all(self):
        self.boxes, self.sel = [], None
        self.refresh()

    def visible(self, b):
        return b[4] <= self.pos < b[5]

    def refresh(self):
        self.area.set_content_height((len(self.boxes) + 1) * (REDACT_ROW + REDACT_GAP) + REDACT_STRIP)
        self.del_btn.set_sensitive(self.sel is not None)
        self.save_btn.set_sensitive(bool(self.boxes))
        self.pos_label.set_label(f"{fmt_tc(self.pos)} / {fmt_tc(self.dur)}")
        self.area.queue_draw()
        self.stage.queue_draw()

    def on_key(self, c, keyval, code, state):
        if Gdk.keyval_name(keyval) in ("Delete", "BackSpace"):
            self.delete_selected()
            return True
        return super().on_key(c, keyval, code, state)

    # ---- vídeo ----
    def on_frame(self, w, h, data):
        import cairo
        self.preview = cairo.ImageSurface.create_for_data(bytearray(data), cairo.FORMAT_RGB24, w, h, w * 4)
        self.pk = self.W / w
        self.stage.queue_draw()

    def on_position(self):
        self.refresh()

    def play_range(self):
        return 0.0, self.dur

    # ---- palco: desenhar e mexer nas caixas ----
    def view_xform(self):
        W, H = self.stage.get_width(), self.stage.get_height()
        k = min((W - 24) / self.W, (H - 24) / self.H)
        return k, (W - self.W * k) / 2, (H - self.H * k) / 2

    def to_img(self, x, y):
        k, ox, oy = self.view_xform()
        return ((x - ox) / k, (y - oy) / k)

    def hit(self, x, y):
        """(índice, alça|'move') sob o ponteiro: alças da caixa escolhida primeiro, depois as caixas visíveis."""
        k, ox, oy = self.view_xform()
        if self.sel is not None and self.visible(self.boxes[self.sel]):
            for name, (hx, hy) in handle_points(self.boxes[self.sel][:4]).items():
                if abs(ox + hx * k - x) <= 14 and abs(oy + hy * k - y) <= 14:
                    return self.sel, name
        px, py = self.to_img(x, y)
        for i in reversed(range(len(self.boxes))):
            x0, y0, x1, y1, *_t = self.boxes[i]
            if self.visible(self.boxes[i]) and x0 <= px <= x1 and y0 <= py <= y1:
                return i, "move"
        return None, None

    def cursor_at(self, x, y):
        _i, h = self.hit(x, y)
        return HANDLE_CURSORS.get(h, "move" if h == "move" else "crosshair")

    def stage_begin(self, _g, x, y):
        self.pause()
        i, h = self.hit(x, y)
        if i is None:  # arrastar no vazio desenha uma caixa nova, valendo o vídeo todo
            px, py = self.to_img(x, y)
            px, py = min(max(px, 0), self.W), min(max(py, 0), self.H)
            self.add_box((px, py, px, py), 0.0, self.dur)
            i, h = self.sel, "new"
        self.drag, self.start = (i, h), (x, y)
        self.start_rect = tuple(self.boxes[i][:4])
        self.select(i)

    def stage_update(self, _g, dx, dy):
        if not self.drag:
            return
        i, h = self.drag
        k = self.view_xform()[0]
        if h == "move":
            r = move_rect(self.start_rect, dx / k, dy / k, self.W, self.H)
        else:
            px, py = self.to_img(self.start[0] + dx, self.start[1] + dy)
            px, py = min(max(px, 0), self.W), min(max(py, 0), self.H)
            if h == "new":
                ax, ay = self.start_rect[:2]
                r = (min(ax, px), min(ay, py), max(ax, px), max(ay, py))
            else:
                r = drag_handle(self.start_rect, h, (px, py), None, self.W, self.H)
        self.boxes[i][:4] = r
        self.stage.queue_draw()

    def stage_end(self):
        if self.drag and self.drag[1] == "new":
            x0, y0, x1, y1 = self.boxes[self.sel][:4]
            if x1 - x0 < CROP_MIN or y1 - y0 < CROP_MIN:  # clique sem arrastar: não cria caixa
                self.delete_selected()
        self.drag = None
        self.refresh()

    def paint_blur(self, cr, x0, y0, x1, y1):
        """Prévia do desfoque: reduz o trecho e amplia com filtro bilinear."""
        import cairo
        if not self.preview or x1 - x0 < 1 or y1 - y0 < 1:
            return
        pw, ph = (x1 - x0) / self.pk, (y1 - y0) / self.pk
        sw, sh = max(1, round(pw / 40)), max(1, round(ph / 40))
        small = cairo.ImageSurface(cairo.FORMAT_RGB24, sw, sh)
        c2 = cairo.Context(small)
        c2.scale(sw / pw, sh / ph)
        c2.set_source_surface(self.preview, -x0 / self.pk, -y0 / self.pk)
        c2.get_source().set_filter(cairo.FILTER_GOOD)
        c2.paint()
        cr.save()
        cr.rectangle(x0, y0, x1 - x0, y1 - y0)
        cr.clip()
        cr.translate(x0, y0)
        cr.scale((x1 - x0) / sw, (y1 - y0) / sh)
        cr.set_source_surface(small, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_BILINEAR)
        cr.get_source().set_extend(cairo.EXTEND_PAD)
        cr.paint()
        cr.restore()

    def draw_stage(self, _a, cr, _w, _h):
        k, ox, oy = self.view_xform()
        cr.save()
        cr.translate(ox, oy)
        cr.scale(k, k)
        if self.preview:
            paint_photo(cr, self.preview, self.pk)
        for b in self.boxes:
            if self.visible(b):
                if self.style == "blur":
                    self.paint_blur(cr, *b[:4])
                else:
                    cr.set_source_rgb(*self.color)
                    cr.rectangle(b[0], b[1], b[2] - b[0], b[3] - b[1])
                    cr.fill()
        cr.restore()
        if self.sel is None:
            return
        b = self.boxes[self.sel]
        X0, Y0, X1, Y1 = ox + b[0] * k, oy + b[1] * k, ox + b[2] * k, oy + b[3] * k
        cr.set_source_rgb(*system_colors()[1])
        cr.set_line_width(2)
        if not self.visible(b):  # fora do intervalo agora: só o contorno tracejado
            cr.set_dash([6, 4])
            cr.rectangle(X0, Y0, X1 - X0, Y1 - Y0)
            cr.stroke()
            return
        cr.rectangle(X0, Y0, X1 - X0, Y1 - Y0)
        cr.stroke()
        for name, (hx, hy) in handle_points((X0, Y0, X1, Y1)).items():
            if len(name) == 2:
                rounded_rect(cr, hx - 6, hy - 6, 12, 12, 2.5)
            elif name in ("n", "s"):
                rounded_rect(cr, hx - 12, hy - 3.5, 24, 7, 3.5)
            else:
                rounded_rect(cr, hx - 3.5, hy - 12, 7, 24, 3.5)
            cr.fill()

    # ---- linha do tempo: uma faixa por caixa, faixa de adicionar e tira de quadros ----
    def tx(self, t):
        return REDACT_GUTTER + (self.area.get_width() - REDACT_GUTTER) * t / self.dur

    def xt(self, x):
        t = (x - REDACT_GUTTER) / max(1, self.area.get_width() - REDACT_GUTTER) * self.dur
        return min(max(round(t * self.fps) / self.fps, 0), self.dur)

    def time_begin(self, _g, x, y):
        self.pause()
        row = int(y // (REDACT_ROW + REDACT_GAP))
        n = len(self.boxes)
        if row < n:
            b = self.boxes[row]
            xs, xe = self.tx(b[4]), self.tx(b[5])
            kind = "t0" if abs(x - xs) <= 10 else "t1" if abs(x - xe) <= 10 else "move" if xs < x < xe else "seek"
            self.tdrag = (kind, row, x, b[4], b[5])
            self.select(row)
        elif row == n:  # faixa vazia: arrastar marca o intervalo da caixa nova
            self.tdrag = ("new", None, x, self.xt(x), self.xt(x))
        else:
            self.tdrag = ("seek", None, x, 0, 0)
        self.time_update(0)

    def time_update(self, dx):
        if not self.tdrag:
            return
        kind, i, x, t0, t1 = self.tdrag
        frame = 1 / self.fps
        if kind == "seek":
            self.seek(self.xt(x + dx))
        elif kind == "new":
            self.new_range = sorted((t0, self.xt(x + dx)))
            self.area.queue_draw()
        else:
            b = self.boxes[i]
            if kind == "t0":
                b[4] = min(self.xt(x + dx), b[5] - frame)
                self.seek(b[4])
            elif kind == "t1":
                b[5] = max(self.xt(x + dx), b[4] + frame)
                self.seek(b[5] - frame)
            else:
                d = min(max(self.xt(x + dx) - self.xt(x), -t0), self.dur - t1)
                b[4], b[5] = t0 + d, t1 + d
                if not self.visible(b):
                    self.seek(b[4])
            self.refresh()

    def time_end(self, dx):
        if self.tdrag and self.tdrag[0] == "new":
            t0, t1 = getattr(self, "new_range", (0, 0))
            if abs(dx) < 4 or t1 - t0 < 1 / self.fps:  # só um clique: a caixa vale o vídeo todo
                t0, t1 = 0.0, self.dur
            self.new_range = None
            r = self.boxes[self.sel][:4] if self.sel is not None else self.default_rect()
            if self.sel is not None:  # caixa nova perto da escolhida, sem cobrir exatamente
                off = min(self.W, self.H) * 0.05
                r = move_rect(r, off, off, self.W, self.H)
            self.add_box(r, t0, t1)
            if not self.visible(self.boxes[-1]):
                self.seek(t0)
        self.tdrag = None

    def draw_timeline(self, _a, cr, w, h):
        dark, accent = system_colors()
        fg = (1, 1, 1) if dark else (0, 0, 0)
        track = (1, 1, 1, 0.06) if dark else (0, 0, 0, 0.05)
        step = REDACT_ROW + REDACT_GAP
        G = REDACT_GUTTER
        rows = [*self.boxes, None]
        for r, b in enumerate(rows):
            y = r * step
            show_centered(cr, label_layout(cr, str(r + 1), 10, False), G / 2 - 3, y + REDACT_ROW / 2, fg)
            cr.set_source_rgba(*track)
            rounded_rect(cr, G, y, w - G, REDACT_ROW, 8)
            cr.fill()
            if b is None:
                nr = getattr(self, "new_range", None)
                if nr:
                    cr.set_source_rgba(*accent, 0.45)
                    rounded_rect(cr, self.tx(nr[0]), y, max(4, self.tx(nr[1]) - self.tx(nr[0])), REDACT_ROW, 8)
                    cr.fill()
                lay = label_layout(cr, "Arraste para adicionar", 10, False)
                show_centered(cr, lay, G + (w - G) / 2, y + REDACT_ROW / 2, tuple(c * 0.55 + 0.2 for c in fg))
                continue
            xs, xe = self.tx(b[4]), self.tx(b[5])
            cr.set_source_rgba(*accent, 0.95 if r == self.sel else 0.5)
            rounded_rect(cr, xs, y, max(4, xe - xs), REDACT_ROW, 8)
            cr.fill()
            cr.set_source_rgb(1, 1, 1)
            for gx in (xs + 7, xe - 7):
                rounded_rect(cr, gx - 1.5, y + 9, 3, REDACT_ROW - 18, 1.5)
                cr.fill()
            lay = label_layout(cr, f"Caixa {r + 1}", 10, True)
            mid = (xs + xe) / 2
            if self.style == "solid":
                cr.set_source_rgb(*self.color)
                cr.rectangle(mid - 38, y + REDACT_ROW / 2 - 5, 10, 10)
                cr.fill()
            show_centered(cr, lay, mid + 6, y + REDACT_ROW / 2, (1, 1, 1))
        y = len(rows) * step
        rounded_rect(cr, G, y, w - G, REDACT_STRIP, 8)
        cr.save()
        cr.clip()
        cr.set_source_rgb(0.09, 0.09, 0.1)
        cr.paint()
        if self.strip:
            paint_strip(cr, self.strip, G, w, y, REDACT_STRIP)
        cr.restore()
        px = self.tx(self.pos)
        cr.set_source_rgba(0, 0, 0, 0.35)
        cr.rectangle(px - 2, 0, 4, h)
        cr.fill()
        cr.set_source_rgb(*fg) if not dark else cr.set_source_rgb(1, 1, 1)
        cr.rectangle(px - 1, 0, 2, h)
        cr.fill()

    def save(self):
        self.pause()
        dst = video_out(self.src, "-oculto")
        self.save_ffmpeg(redact_cmd(self.src, [tuple(b) for b in self.boxes], self.style, self.color, dst), dst)


# ---------------- Velocidade do vídeo ----------------
SPEEDS = (0.25, 0.5, 1, 1.5, 2, 4)


def open_speed(src):
    SpeedChanger(Gio.Application.get_default(), Path(src)).present()


class SpeedChanger(VideoPlayer, PhotoTool, Adw.ApplicationWindow):
    """Velocidade: cópia mais lenta ou mais rápida; o áudio mantém o tom e vídeos sem som também funcionam."""

    def __init__(self, app, src):
        super().__init__(application=app, title=f"Velocidade — {src.name}", default_width=450, default_height=580)
        load_css(EDITOR_CSS, TRIM_CSS)
        self.src = src
        self.fps, self.dur = probe_video(src)[2:]
        self.audio = has_audio(src)
        header = tool_header(self, "Velocidade", src.name, "Salvar cópia", lambda: self.scale.set_value(2))

        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, hexpand=True, vexpand=True)
        stage = Gtk.Box(margin_top=12, margin_start=12, margin_end=12)
        stage.add_css_class("photo-stage")
        stage.set_overflow(Gtk.Overflow.HIDDEN)
        stage.append(self.picture)

        name = Gtk.Label(label="Velocidade", xalign=0, hexpand=True)
        self.value = Gtk.Label()
        self.value.add_css_class("title-3")
        top = Gtk.Box(margin_top=14, margin_start=14, margin_end=14)
        top.append(name)
        top.append(self.value)
        # posições igualmente espaçadas, uma por velocidade (0,25× a 4× numa escala linear ficaria toda espremida)
        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, len(SPEEDS) - 1, 1)
        self.scale.set_draw_value(False)
        self.scale.set_round_digits(0)
        self.scale.set_margin_start(24)
        self.scale.set_margin_end(24)
        for i, sp in enumerate(SPEEDS):
            self.scale.add_mark(i, Gtk.PositionType.BOTTOM, f"{sp:g}×")
        self.scale.connect("value-changed", lambda sc: self.set_speed(SPEEDS[round(sc.get_value())]))

        controls = Gtk.Box(spacing=4, margin_top=10, margin_bottom=14, margin_start=10, margin_end=14)
        self.player_buttons(controls)
        self.pos_label = Gtk.Label(hexpand=True, xalign=1)
        self.pos_label.add_css_class("timecode")
        self.pos_label.add_css_class("dim-label")
        controls.append(self.pos_label)
        note = Gtk.Label(label="O áudio mantém o tom original." if self.audio else "Vídeo sem som.", xalign=0,
                         margin_start=14, margin_top=4)
        note.add_css_class("dim-label")
        note.add_css_class("caption")

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for wdg in (stage, top, self.scale, note, controls):
            body.append(wdg)
        view = Adw.ToolbarView(content=body)
        view.add_top_bar(header)
        self.toast = Adw.ToastOverlay(child=view)
        self.set_content(self.toast)
        self.setup_player(src)
        self.scale.set_value(SPEEDS.index(1))
        self.set_speed(1)

    def set_speed(self, sp):
        self.speed = sp
        self.value.set_label(f"{sp:g}×")
        if self.rate != sp:
            self.rate = sp
            self.seek(self.pos)  # nova taxa vale a partir de um seek

    def on_frame(self, w, h, data):
        self.picture.set_paintable(Gdk.MemoryTexture.new(w, h, Gdk.MemoryFormat.B8G8R8X8, GLib.Bytes.new(data), w * 4))

    def on_position(self):
        # tempos na linha do tempo da cópia (4 s a 2× viram 2 s)
        self.pos_label.set_label(f"{fmt_tc(self.pos / self.speed)} / {fmt_tc(self.dur / self.speed)}")

    def play_range(self):
        return 0.0, self.dur

    def save(self):
        dst = video_out(self.src, f"-{self.speed:g}x")
        self.pause()
        self.save_ffmpeg(speed_cmd(self.src, self.speed, self.fps, dst, self.audio), dst)


# ---------------- Quadros ----------------
SNAP_CSS = b"""
.snap { border-radius: 10px; background: #161618; }
.snap-time { background: rgba(0,0,0,.65); color: white; border-radius: 6px; padding: 1px 6px; margin: 6px;
             font-family: monospace; font-feature-settings: "tnum"; font-weight: bold; }
.snap-x { min-width: 22px; min-height: 22px; padding: 0; margin: 5px; background: rgba(0,0,0,.65); color: white; }
"""


def frame_start(t, fps):
    """Início do quadro que está na tela no instante t."""
    return math.floor(t * fps + 1e-3) / fps


def save_frame(src, t, fps):
    """PNG em resolução cheia do quadro que começa em t, ao lado do vídeo."""
    dst = out_path(src, "png", f"-{t:.2f}s")
    # -ss meio milissegundo antes: o ffmpeg entrega o primeiro quadro com pts >= ss, ou seja, este
    video_frame(src, t - 0.5 / max(fps, 1000)).save(dst)
    return dst


def open_snapshots(src):
    Snapshots(Gio.Application.get_default(), Path(src)).present()


class Snapshots(VideoPlayer, PhotoTool, Adw.ApplicationWindow):
    """Quadros: escolhe quadros exatos (passo a passo) e salva cada um como PNG em resolução cheia."""

    def __init__(self, app, src):
        super().__init__(application=app, title=f"Quadros — {src.name}", default_width=450, default_height=610)
        load_css(EDITOR_CSS, TRIM_CSS, SNAP_CSS)
        self.src = src
        self.fps, self.dur = probe_video(src)[2:]
        self.frames = {}  # tempo -> textura da miniatura
        self.shown = None
        header = tool_header(self, "Quadros", src.name, "Salvar", self.reset_all)

        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, hexpand=True, vexpand=True)
        stage = Gtk.Box(margin_top=12, margin_start=12, margin_end=12)
        stage.add_css_class("photo-stage")
        stage.set_overflow(Gtk.Overflow.HIDDEN)
        stage.append(self.picture)

        self.syncing_scrub = False
        self.scrub = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, max(self.dur, 0.01), 0.01)
        self.scrub.set_draw_value(False)
        self.scrub.set_margin_start(14)
        self.scrub.set_margin_end(14)
        self.scrub.set_margin_top(10)
        self.scrub.connect("value-changed", lambda sc: self.syncing_scrub or (self.pause(), self.seek(sc.get_value())))
        controls = Gtk.Box(spacing=4, margin_start=10, margin_end=14)
        self.player_buttons(controls)
        self.pos_label = Gtk.Label(hexpand=True, xalign=1)
        self.pos_label.add_css_class("timecode")
        self.pos_label.add_css_class("dim-label")
        controls.append(self.pos_label)

        bar = Gtk.Box(spacing=8, margin_top=8, margin_start=14, margin_end=14)
        self.count = Gtk.Label(hexpand=True, xalign=0)
        self.count.add_css_class("dim-label")
        add = Gtk.Button(label="Adicionar quadro", tooltip_text="Adicionar quadro (Enter)")
        add.connect("clicked", lambda _b: self.add_frame())
        bar.append(self.count)
        bar.append(add)
        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, min_children_per_line=3, max_children_per_line=8,
                                halign=Gtk.Align.START,
                                column_spacing=8, row_spacing=8, valign=Gtk.Align.START)
        strip = Gtk.ScrolledWindow(child=self.flow, hscrollbar_policy=Gtk.PolicyType.NEVER, min_content_height=96,
                                   max_content_height=200, propagate_natural_height=True,
                                   margin_top=8, margin_bottom=14, margin_start=14, margin_end=14)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for wdg in (stage, self.scrub, controls, Gtk.Separator(margin_top=8, margin_start=14, margin_end=14), bar,
                    strip):
            body.append(wdg)
        view = Adw.ToolbarView(content=body)
        view.add_top_bar(header)
        self.toast = Adw.ToastOverlay(child=view)
        self.set_content(self.toast)
        self.setup_player(src)
        self.refresh()
        self.on_position()

    def on_key(self, c, keyval, code, state):
        if Gdk.keyval_name(keyval) in ("Return", "KP_Enter"):
            self.add_frame()
            return True
        return super().on_key(c, keyval, code, state)

    def on_frame(self, w, h, data):
        self.shown = (w, h, data)
        self.picture.set_paintable(Gdk.MemoryTexture.new(w, h, Gdk.MemoryFormat.B8G8R8X8, GLib.Bytes.new(data), w * 4))

    def on_position(self):
        self.syncing_scrub = True
        self.scrub.set_value(self.pos)
        self.syncing_scrub = False
        self.pos_label.set_label(f"{fmt_tc(self.pos)} / {fmt_tc(self.dur)}")

    def play_range(self):
        return 0.0, self.dur

    def add_frame(self):
        self.pause()
        if not self.shown:
            return
        t = round(frame_start(self.pos, self.fps), 3)
        if t in self.frames:
            self.toast.add_toast(Adw.Toast(title="Esse quadro já está na lista", timeout=2))
            return
        from PIL import Image, ImageOps
        w, h, data = self.shown
        # miniatura pronta no tamanho do cartão: um Gtk.Picture pede o tamanho natural e esticaria o cartão
        self.frames[t] = pil_texture(ImageOps.fit(Image.frombuffer("RGB", (w, h), data, "raw", "BGRX", 0, 1), (128, 80)))
        self.refresh()

    def remove(self, t):
        del self.frames[t]
        self.refresh()

    def refresh(self):
        self.flow.remove_all()
        for t in sorted(self.frames):
            pic = Gtk.Picture(paintable=self.frames[t])
            card = Gtk.Overlay(child=pic, tooltip_text="Ir para este quadro", halign=Gtk.Align.START)
            card.add_css_class("snap")
            card.set_overflow(Gtk.Overflow.HIDDEN)
            tc = Gtk.Label(label=fmt_tc(t), halign=Gtk.Align.START, valign=Gtk.Align.END)
            tc.add_css_class("snap-time")
            card.add_overlay(tc)
            x = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Remover", halign=Gtk.Align.END,
                           valign=Gtk.Align.START)
            x.add_css_class("circular")
            x.add_css_class("snap-x")
            x.connect("clicked", lambda _b, t=t: self.remove(t))
            card.add_overlay(x)
            click = Gtk.GestureClick()
            click.connect("released", lambda *_a, t=t: (self.pause(), self.seek(t)))
            pic.add_controller(click)
            self.flow.append(card)
        n = len(self.frames)
        self.count.set_label("Nenhum quadro escolhido" if not n else f"{n} selecionado" + ("s" if n > 1 else ""))
        self.save_label = "Salvar" if not n else f"Salvar {n} quadro" + ("s" if n > 1 else "")
        self.save_btn.set_label(self.save_label)
        self.save_btn.set_sensitive(n > 0)

    def reset_all(self):
        self.frames.clear()
        self.refresh()

    def save(self):
        self.pause()
        times = sorted(self.frames)
        self.save_btn.set_sensitive(False)
        self.save_btn.set_label("Salvando…")

        def work():
            try:
                for t in times:
                    save_frame(self.src, t, self.fps)
                msg = f"{len(times)} PNG salvo" + ("s" if len(times) > 1 else "") + " na pasta do vídeo"
            except Exception as ex:
                msg = f"Erro ao salvar: {ex}"
            GLib.idle_add(self._saved, msg)
        threading.Thread(target=work, daemon=True).start()


# ---------------- Normalizar volume ----------------
WAVE_BARS = 140


def open_normalizer(src):
    Normalizer(Gio.Application.get_default(), Path(src)).present()


def fmt_loud(m, prefix):
    return (f"{prefix}: {m['input_i']:.1f} LUFS · faixa {m['input_lra']:.1f} LU · pico {m['input_tp']:.1f} dBTP"
            .replace(".", ","))


class Normalizer(VideoPlayer, PhotoTool, Adw.ApplicationWindow):
    """Normalizar volume: loudness alvo (EBU R128), faixa dinâmica e pico verdadeiro; mostra entrada e saída medidas."""
    fps = 100  # o player anda em passos de 10 ms (áudio não tem quadros)

    def __init__(self, app, src):
        super().__init__(application=app, title=f"Normalizar volume — {src.name}", default_width=480)
        load_css(EDITOR_CSS, TRIM_CSS)
        self.src = src
        self.dur = media_duration(src)
        self.target, self.lra, self.tp = -16, 11, -1.5
        self.peaks, self.measured = None, None
        header = tool_header(self, "Volume", src.name, "Normalizar", self.reset_all)

        top = Gtk.Box(margin_top=12, margin_start=14, margin_end=14)
        name = Gtk.Label(label=src.name, xalign=0, hexpand=True, ellipsize=3)
        name.add_css_class("dim-label")
        length = Gtk.Label(label=fmt_tc(self.dur))
        length.add_css_class("timecode")
        length.add_css_class("dim-label")
        top.append(name)
        top.append(length)

        self.area = Gtk.DrawingArea(height_request=110, margin_top=8, margin_start=12, margin_end=12)
        self.area.set_draw_func(self.draw)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", lambda _g, x, _y: self.seek_x(x))
        drag.connect("drag-update", lambda g, dx, _dy: self.seek_x(g.get_start_point()[1] + dx))
        self.area.add_controller(drag)

        controls = Gtk.Box(spacing=4, margin_top=6, margin_start=10, margin_end=14)
        self.play_btn = Gtk.Button(icon_name="media-playback-start-symbolic", tooltip_text="Tocar (Espaço)",
                                   valign=Gtk.Align.CENTER)
        self.play_btn.add_css_class("flat")
        self.play_btn.add_css_class("circular")
        self.play_btn.connect("clicked", lambda _b: self.toggle())
        self.pos_label = Gtk.Label(hexpand=True, xalign=1)
        self.pos_label.add_css_class("timecode")
        self.pos_label.add_css_class("dim-label")
        controls.append(self.play_btn)
        controls.append(self.pos_label)

        self.scales = []
        rows = []
        for label, lo, hi, step, fmt, key in (("Volume alvo", -30, -5, 1, "{:g} LUFS", "target"),
                                              ("Faixa dinâmica", 1, 20, 1, "{:g} LU", "lra"),
                                              ("Pico máximo", -9, 0, 0.5, "{:g} dBTP", "tp")):
            row, scale = slider_row(label, lo, hi, step, fmt, getattr(self, key),
                                    lambda x, key=key: setattr(self, key, x))
            rows.append(row)
            self.scales.append(scale)
        sliders = Adw.Clamp(child=boxed_list(rows), margin_top=12, margin_start=12, margin_end=12, maximum_size=900)

        self.levels = [Gtk.Label(xalign=0, wrap=True, margin_start=14, margin_end=14) for _ in range(2)]
        self.levels[0].set_label("Entrada: medindo…")
        self.levels[0].set_margin_top(12)
        note = Gtk.Label(label="−16 LUFS é um bom padrão para voz e música. O pico máximo evita distorção depois "
                               "da compressão do arquivo.", xalign=0, wrap=True, margin_top=10, margin_bottom=14,
                         margin_start=14, margin_end=14)
        note.add_css_class("dim-label")
        note.add_css_class("caption")

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for wdg in (top, self.area, controls, sliders, *self.levels, note):
            body.append(wdg)
        view = Adw.ToolbarView(content=body)
        view.add_top_bar(header)
        self.toast = Adw.ToastOverlay(child=view)
        self.set_content(self.toast)
        self.setup_player(src)
        self.on_position()
        threading.Thread(target=self.analyze, daemon=True).start()

    def analyze(self):
        try:
            peaks = waveform(self.src, WAVE_BARS)
            GLib.idle_add(lambda: (setattr(self, "peaks", peaks), self.area.queue_draw()) and False)
            m = loudness(self.src)
            GLib.idle_add(lambda: (setattr(self, "measured", m), self.levels[0].set_label(fmt_loud(m, "Entrada")))
                          and False)
        except (subprocess.CalledProcessError, ValueError):
            GLib.idle_add(lambda: self.levels[0].set_label("Entrada: não deu para medir este arquivo") and False)

    def reset_all(self):
        for scale, x in zip(self.scales, (-16, 11, -1.5)):
            scale.set_value(x)

    # ---- player ----
    def on_frame(self, w, h, data):
        pass  # só áudio

    def on_position(self):
        self.pos_label.set_label(f"{fmt_tc(self.pos)} / {fmt_tc(self.dur)}")
        self.area.queue_draw()

    def play_range(self):
        return 0.0, self.dur

    def seek_x(self, x):
        self.seek(min(max(x / max(1, self.area.get_width()), 0), 1) * self.dur)

    def draw(self, _a, cr, w, h):
        dark, accent = system_colors()
        rounded_rect(cr, 0, 0, w, h, 12)
        cr.set_source_rgba(*((1, 1, 1, 0.05) if dark else (0, 0, 0, 0.05)))
        cr.fill()
        if self.peaks:
            cell = (w - 24) / len(self.peaks)
            cr.set_source_rgb(*accent)
            for i, p in enumerate(self.peaks):
                x = 12 + i * cell + cell / 2
                bh = p * (h * 0.45)
                if bh < 2:  # silêncio: ponto
                    cr.arc(x, h / 2, 1.1, 0, math.tau)
                    cr.fill()
                else:
                    rounded_rect(cr, x - cell * 0.3, h / 2 - bh, cell * 0.6, bh * 2, min(cell * 0.3, 1.5))
                    cr.fill()
        px = 12 + (w - 24) * self.pos / max(self.dur, 0.01)
        cr.set_source_rgb(*((1, 1, 1) if dark else (0.35, 0.35, 0.38)))
        cr.rectangle(px - 1, 6, 2, h - 12)
        cr.fill()

    # ---- salvar ----
    def save(self):
        self.pause()
        self.save_btn.set_sensitive(False)
        self.save_btn.set_label("Normalizando…")
        target, lra, tp = self.target, self.lra, self.tp

        def work():
            out = None
            try:
                m = self.measured or loudness(self.src)
                dst = normalize_out(self.src)
                r = subprocess.run(normalize_cmd(self.src, target, lra, tp, m, dst), capture_output=True, text=True)
                if r.returncode:
                    raise RuntimeError((r.stderr.strip().splitlines() or ["ffmpeg falhou"])[-1])
                out = loudness(dst)
                msg = f"Salvo: {dst.name}"
            except Exception as ex:
                msg = f"Erro ao salvar: {ex}"
            GLib.idle_add(lambda: (out and self.levels[1].set_label(fmt_loud(out, "Saída")), self._saved(msg))
                          and False)
        threading.Thread(target=work, daemon=True).start()


# ---------------- Visualizador de áudio ----------------
def open_visualizer(src):
    Visualizer(Gio.Application.get_default(), Path(src)).present()


class Visualizer(PhotoTool, Adw.ApplicationWindow):
    """Visualizador: transforma o áudio num MP4 com onda animada ou imagem parada, em paisagem, retrato ou quadrado."""

    def __init__(self, app, src):
        super().__init__(application=app, title=f"Visualizador — {src.name}", default_width=480, default_height=560)
        self.src = src
        self.style, self.shape, self.image, self.image_path = 0, 0, None, None
        self.peaks = None
        header = tool_header(self, "Visualizador", src.name, "Criar vídeo", self.reset_all)

        self.area = Gtk.DrawingArea(vexpand=True, height_request=220, margin_top=12, margin_start=12, margin_end=12)
        self.area.set_draw_func(self.draw)
        self.style_row = Adw.ComboRow(title="Estilo", model=Gtk.StringList.new(["Onda animada", "Imagem parada"]))
        self.style_row.connect("notify::selected", lambda r, _p: self.set_style(r.get_selected()))
        self.shape_row = Adw.ComboRow(title="Formato", model=Gtk.StringList.new([n for n, _s in VIZ_SHAPES]))
        self.shape_row.connect("notify::selected", lambda r, _p: (setattr(self, "shape", r.get_selected()),
                                                                  self.area.queue_draw()))
        self.image_row = Adw.ActionRow(title="Imagem", subtitle="Nenhuma escolhida", visible=False)
        pick = Gtk.Button(label="Escolher…", valign=Gtk.Align.CENTER)
        pick.connect("clicked", lambda _b: self.pick_image())
        self.image_row.add_suffix(pick)
        rows = Adw.Clamp(child=boxed_list([self.style_row, self.shape_row, self.image_row]), margin_top=12,
                         margin_bottom=12, margin_start=12, margin_end=12, maximum_size=900)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(self.area)
        body.append(rows)
        view = Adw.ToolbarView(content=body)
        view.add_top_bar(header)
        self.toast = Adw.ToastOverlay(child=view)
        self.set_content(self.toast)
        threading.Thread(target=self.load, daemon=True).start()

    def load(self):
        try:
            peaks = waveform(self.src, 48)
        except subprocess.CalledProcessError:
            peaks = None
        cover = audio_cover(self.src)
        GLib.idle_add(self.loaded, peaks, cover)

    def loaded(self, peaks, cover):
        self.peaks = peaks
        if cover and not self.image:  # capa embutida já serve de imagem parada
            self.set_image(cover, None, "Capa do arquivo")
        self.area.queue_draw()

    def set_style(self, i):
        self.style = i
        self.image_row.set_visible(i == 1)
        self.save_btn.set_sensitive(i == 0 or self.image is not None)
        self.area.queue_draw()

    def set_image(self, im, path, name):
        self.image, self.image_path = im, path
        k = min(1, 800 / max(im.size))
        self.image_surf = pil_to_surface(im.resize((max(1, round(im.width * k)), max(1, round(im.height * k)))))
        self.image_row.set_subtitle(name)
        self.set_style(self.style)

    def pick_image(self):
        f = Gtk.FileFilter(name="Imagens")
        f.add_pixbuf_formats()

        def chosen(d, res):
            try:
                from PIL import Image, ImageOps
                path = Path(d.open_finish(res).get_path())
                self.set_image(flatten(ImageOps.exif_transpose(Image.open(path))), path, path.name)
            except (GLib.Error, OSError):
                pass
        Gtk.FileDialog(title="Imagem do vídeo", default_filter=f).open(self, None, chosen)

    def reset_all(self):
        self.style_row.set_selected(0)
        self.shape_row.set_selected(0)

    def draw(self, _a, cr, w, h):
        W, H = VIZ_SHAPES[self.shape][1]
        k = min((w - 8) / W, (h - 8) / H)
        fw, fh = W * k, H * k
        x0, y0 = (w - fw) / 2, (h - fh) / 2
        rounded_rect(cr, x0, y0, fw, fh, 12)
        cr.set_source_rgb(0, 0, 0)
        cr.fill_preserve()
        cr.clip()
        if self.style == 1:
            if self.image:
                s = self.image_surf
                ik = min(fw / s.get_width(), fh / s.get_height())
                cr.translate(x0 + (fw - s.get_width() * ik) / 2, y0 + (fh - s.get_height() * ik) / 2)
                cr.scale(ik, ik)
                cr.set_source_surface(s, 0, 0)
                cr.paint()
            else:
                lay = label_layout(cr, "Escolha uma imagem", 11, False)
                show_centered(cr, lay, w / 2, h / 2, (0.7, 0.7, 0.72))
            return
        if not self.peaks:
            return
        cr.set_source_rgb(*system_colors()[1])
        n = len(self.peaks)
        cell = fw * 0.9 / n
        for i, p in enumerate(self.peaks):  # prévia: onda do áudio inteiro em barras; o vídeo anima com o som
            x = x0 + fw * 0.05 + i * cell + cell / 2
            bh = p * fh * 0.25
            if bh < 2:
                cr.arc(x, y0 + fh / 2, max(1.2, cell * 0.18), 0, math.tau)
                cr.fill()
            else:
                rounded_rect(cr, x - cell * 0.18, y0 + fh / 2 - bh, cell * 0.36, bh * 2, cell * 0.18)
                cr.fill()

    def save(self):
        dst = out_path(self.src, "mp4", "-visualizador")
        image = self.image_path
        if self.style == 1 and image is None:  # capa embutida: o ffmpeg precisa dela num arquivo (sobrescrito a cada uso)
            image = Path(GLib.get_user_runtime_dir()) / "bergamota-capa.png"
            self.image.save(image)
        self.save_ffmpeg(visualizer_cmd(self.src, VIZ_SHAPES[self.shape][1], system_colors()[1],
                                        image if self.style == 1 else None, dst), dst)


# ---------------- Cortar áudio ----------------
def open_audio_trimmer(src):
    AudioTrimmer(Gio.Application.get_default(), Path(src)).present()


class AudioTrimmer(Trimmer):
    """Cortar áudio: o corte do vídeo sobre a forma de onda, com tempos exatos e remoção do silêncio das pontas."""
    fps = 100  # passos de 10 ms (áudio não tem quadros)

    def __init__(self, app, src):
        Adw.ApplicationWindow.__init__(self, application=app, title=f"Cortar áudio — {src.name}", default_width=480)
        load_css(EDITOR_CSS, TRIM_CSS)
        self.src = src
        self.dur = media_duration(src)
        self.start, self.end, self.pos = 0.0, self.dur, 0.0
        self.drag, self.strip, self.peaks, self.silence = None, None, None, None
        header = tool_header(self, "Cortar áudio", src.name, "Salvar trecho", self.reset_all)

        hint = Gtk.Label(label="Arraste as pontas para cortar", xalign=0, hexpand=True)
        hint.add_css_class("dim-label")
        self.sel_label = Gtk.Label()
        self.sel_label.add_css_class("dim-label")
        info = Gtk.Box(margin_top=12, margin_start=14, margin_end=14)
        info.append(hint)
        info.append(self.sel_label)

        self.area = Gtk.DrawingArea(height_request=110, margin_top=8, margin_start=12, margin_end=12)
        self.area.set_draw_func(self.draw)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self.on_begin)
        drag.connect("drag-update", lambda _g, dx, _dy: self.drag_to(self.drag_x + dx))
        drag.connect("drag-end", lambda *_: setattr(self, "drag", None))
        self.area.add_controller(drag)

        tc = Gtk.Box(spacing=8, margin_top=10, margin_start=14, margin_end=14)
        self.entries = {}
        for which, label in (("start", "Início"), ("end", "Fim")):
            if which == "end":
                tc.append(Gtk.Box(hexpand=True))
            name = Gtk.Label(label=label)
            name.add_css_class("dim-label")
            entry = Gtk.Entry(width_chars=9, max_width_chars=9, xalign=0.5, tooltip_text="m:ss.cc")
            entry.add_css_class("timecode")
            entry.connect("activate", lambda e, w=which: self.commit(e, w))
            leave = Gtk.EventControllerFocus()
            leave.connect("leave", lambda _c, e=entry, w=which: self.commit(e, w))
            entry.add_controller(leave)
            self.entries[which] = entry
            tc.append(name)
            tc.append(entry)

        self.trim_silence = Gtk.CheckButton(label="Tirar o silêncio do começo e do fim", margin_top=12,
                                            margin_start=10, sensitive=False)
        self.trim_silence.connect("toggled", lambda c: self.apply_silence(c.get_active()))
        controls = Gtk.Box(spacing=4, margin_top=8, margin_bottom=14, margin_start=10, margin_end=14)
        self.play_btn = Gtk.Button(icon_name="media-playback-start-symbolic", tooltip_text="Tocar trecho (Espaço)",
                                   valign=Gtk.Align.CENTER)
        self.play_btn.add_css_class("flat")
        self.play_btn.add_css_class("circular")
        self.play_btn.connect("clicked", lambda _b: self.toggle())
        self.pos_label = Gtk.Label(hexpand=True, xalign=1)
        self.pos_label.add_css_class("timecode")
        self.pos_label.add_css_class("dim-label")
        controls.append(self.play_btn)
        controls.append(self.pos_label)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for wdg in (info, self.area, tc, self.trim_silence, controls):
            body.append(wdg)
        view = Adw.ToolbarView(content=body)
        view.add_top_bar(header)
        self.toast = Adw.ToastOverlay(child=view)
        self.set_content(self.toast)
        self.setup_player(src)
        threading.Thread(target=self.analyze, daemon=True).start()
        self.refresh()

    def analyze(self):
        try:
            peaks = waveform(self.src, WAVE_BARS)
            silence = silent_ends(self.src, self.dur)
        except subprocess.CalledProcessError:
            return
        GLib.idle_add(self.analyzed, peaks, silence)

    def analyzed(self, peaks, silence):
        self.peaks, self.silence = peaks, silence
        self.trim_silence.set_sensitive(silence != (0.0, self.dur))
        self.trim_silence.set_tooltip_text(None if self.trim_silence.get_sensitive() else "Não há silêncio nas pontas")
        self.area.queue_draw()

    def apply_silence(self, on):
        self.pause()
        self.start, self.end = self.silence if on else (0.0, self.dur)
        self.seek(self.start)

    def reset_all(self):
        self.trim_silence.set_active(False)
        Trimmer.reset_all(self)

    def on_frame(self, w, h, data):
        pass  # só áudio

    def paint_track(self, cr, x0, x1, h):
        if not self.peaks:
            return
        cell = (x1 - x0) / len(self.peaks)
        cr.set_source_rgb(*system_colors()[1])
        for i, p in enumerate(self.peaks):
            x = x0 + i * cell + cell / 2
            bh = p * h * 0.4
            if bh < 2:
                cr.arc(x, h / 2, 1, 0, math.tau)
            else:
                rounded_rect(cr, x - cell * 0.3, h / 2 - bh, cell * 0.6, bh * 2, min(cell * 0.3, 1.5))
            cr.fill()

    def save(self):
        dst = audio_out(self.src, "-cortado")
        self.pause()
        self.save_ffmpeg(audio_trim_cmd(self.src, self.start, self.end, dst), dst)


class AudioPreview:
    """Toca um arquivo para comparar (a janela define on_play_pos(t) se quiser acompanhar o tempo)."""
    player, playing_btn = None, None

    def on_play_pos(self, t):
        pass

    def render_preview(self, cmd, path, btn, start=0.0):
        """Gera o arquivo da prévia numa thread e toca; o botão vira ■ enquanto isso."""
        self.stop()
        self.playing_btn = btn
        self.set_btn_icon(btn, "media-playback-stop-symbolic")

        def work():
            ok = subprocess.run(cmd, capture_output=True).returncode == 0
            GLib.idle_add(lambda: self.playing_btn is btn and (self.start_player(path, start) if ok else self.stop())
                          and False)
        threading.Thread(target=work, daemon=True).start()

    def start_player(self, path, start=0.0):
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
        Gst.init(None)
        self.player = Gst.ElementFactory.make("playbin")
        self.player.set_property("uri", path.as_uri())
        self.player.set_property("video-sink", Gst.ElementFactory.make("fakesink"))  # capa do arquivo não abre janela
        bus = self.player.get_bus()
        bus.add_signal_watch()
        bus.connect("message::eos", lambda *_: self.stop())
        self.player.set_state(Gst.State.PAUSED)
        self.player.get_state(Gst.SECOND)
        if start:
            self.player.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE, int(start * 1e9))
        self.player.set_state(Gst.State.PLAYING)
        GLib.timeout_add(40, self.play_tick, self.player)

    def play_tick(self, player):
        if player is not self.player:
            return False
        from gi.repository import Gst
        ok, ns = player.query_position(Gst.Format.TIME)
        if ok:
            self.on_play_pos(ns / 1e9)
        return True

    def stop(self):
        if self.player:
            from gi.repository import Gst
            self.player.set_state(Gst.State.NULL)
            self.player.get_bus().remove_signal_watch()
            self.player = None
        if self.playing_btn:
            self.set_btn_icon(self.playing_btn, "media-playback-start-symbolic")
            self.playing_btn = None

    @staticmethod
    def set_btn_icon(btn, icon):
        child = btn.get_child()
        child.set_icon_name(icon) if isinstance(child, Adw.ButtonContent) else btn.set_icon_name(icon)


# ---------------- Canais de áudio ----------------
def open_channels(src):
    Channels(Gio.Application.get_default(), Path(src)).present()


class Channels(AudioPreview, PhotoTool, Adw.ApplicationWindow):
    """Canais: mono ou estéreo, volume de cada lado e prévia de cada canal sozinho ou do resultado."""
    PREVIEW_S = 30  # a prévia toca os primeiros segundos

    def __init__(self, app, src):
        super().__init__(application=app, title=f"Canais — {src.name}", default_width=480)
        load_css(EDITOR_CSS)
        self.src, self.n = src, audio_channels(src)
        self.out, self.vl, self.vr = "stereo", 1.0, 1.0
        self.peaks = None
        header = tool_header(self, "Canais", src.name, "Salvar cópia", self.reset_all)

        title = Gtk.Label(label="Prévia dos canais de saída", xalign=0, margin_top=12, margin_start=14)
        title.add_css_class("dim-label")
        tracks = Gtk.Grid(column_spacing=8, row_spacing=8, margin_top=8, margin_start=14, margin_end=10)
        self.rows = {}
        for r, key in enumerate(("L", "R")):
            badge = Gtk.Label(label=key, width_chars=2)
            badge.add_css_class("heading")
            area = Gtk.DrawingArea(height_request=56, hexpand=True)
            area.set_draw_func(lambda _a, cr, w, h, key=key: self.draw_channel(cr, w, h, key))
            play = Gtk.Button(icon_name="media-playback-start-symbolic", valign=Gtk.Align.CENTER,
                              tooltip_text="Ouvir só este canal")
            play.add_css_class("flat")
            play.add_css_class("circular")
            play.connect("clicked", lambda b, key=key: self.preview(key, b))
            for c, wdg in enumerate((badge, area, play)):
                tracks.attach(wdg, c, r, 1, 1)
            self.rows[key] = (badge, area, play)

        outs = Gtk.Box(halign=Gtk.Align.END, hexpand=True)
        outs.add_css_class("linked")
        first = None
        for key, name in (("mono", "Mono"), ("stereo", "Estéreo")):
            b = Gtk.ToggleButton(label=name, active=key == "stereo", group=first)
            first = first or b
            b.connect("toggled", lambda b, key=key: b.get_active() and self.set_out(key))
            outs.append(b)
        self.stereo_btn = b
        orow = Gtk.Box(spacing=10, margin_start=14, margin_end=14, margin_top=8, margin_bottom=8)
        orow.append(Gtk.Label(label="Saída", xalign=0))
        orow.append(outs)
        lrow, self.lscale = slider_row("Volume esquerdo", 0, 100, 1, "{:g}%", 100, lambda x: self.set_vol("vl", x))
        rrow, self.rscale = slider_row("Volume direito", 0, 100, 1, "{:g}%", 100, lambda x: self.set_vol("vr", x))
        self.play_all = Gtk.Button(child=Adw.ButtonContent(icon_name="media-playback-start-symbolic",
                                                            label="Tocar resultado"), halign=Gtk.Align.END,
                                   hexpand=True)
        self.play_all.connect("clicked", lambda b: self.preview(self.out, b))
        prow = Gtk.Box(spacing=10, margin_start=14, margin_end=14, margin_top=6, margin_bottom=6)
        prow.append(Gtk.Label(label="Prévia", xalign=0))
        prow.append(self.play_all)
        settings = Adw.Clamp(child=boxed_list([Gtk.ListBoxRow(child=orow, activatable=False, selectable=False),
                                               lrow, rrow,
                                               Gtk.ListBoxRow(child=prow, activatable=False, selectable=False)]),
                             margin_top=14, margin_bottom=14, margin_start=12, margin_end=12, maximum_size=900)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for wdg in (title, tracks, settings):
            body.append(wdg)
        view = Adw.ToolbarView(content=body)
        view.add_top_bar(header)
        self.toast = Adw.ToastOverlay(child=view)
        self.set_content(self.toast)
        self.connect("close-request", lambda _w: self.stop() or False)
        threading.Thread(target=self.analyze, daemon=True).start()
        self.set_out("stereo")

    def analyze(self):
        try:
            peaks = waveform(self.src, WAVE_BARS, channels=True)
        except subprocess.CalledProcessError:
            return
        GLib.idle_add(lambda: (setattr(self, "peaks", peaks), self.redraw()) and False)

    # ---- estado ----
    def set_out(self, key):
        self.out = key
        badge, _area, play = self.rows["R"]
        for wdg in (badge, _area, play):
            wdg.set_visible(key == "stereo")
        self.rows["L"][0].set_label("L" if key == "stereo" else "M")
        self.rows["L"][2].set_tooltip_text("Ouvir só este canal" if key == "stereo" else "Ouvir o resultado")
        self.stop()
        self.redraw()

    def set_vol(self, key, x):
        setattr(self, key, x / 100)
        self.stop()
        self.redraw()

    def reset_all(self):
        self.stereo_btn.set_active(True)
        self.lscale.set_value(100)
        self.rscale.set_value(100)

    def redraw(self):
        for _b, area, _p in self.rows.values():
            area.queue_draw()

    def track_peaks(self, key):
        """Picos de saída da linha: o canal com o volume aplicado, ou a mistura no mono."""
        L, R = self.peaks
        if self.out == "stereo":
            return [p * (self.vl if key == "L" else self.vr) for p in (L if key == "L" else R)]
        s = max(self.vl + self.vr, 1)
        return [(a * self.vl + b * self.vr) / s for a, b in zip(L, R)]

    def draw_channel(self, cr, w, h, key):
        dark, accent = system_colors()
        rounded_rect(cr, 0, 0, w, h, 10)
        cr.set_source_rgba(*((1, 1, 1, 0.05) if dark else (0, 0, 0, 0.05)))
        cr.fill()
        cr.set_source_rgb(*accent)
        cr.rectangle(0, 4, 3, h - 8)
        cr.fill()
        if not self.peaks:
            return
        peaks = self.track_peaks(key)
        cell = (w - 20) / len(peaks)
        cr.set_source_rgba(*((1, 1, 1, 0.55) if dark else (0, 0, 0, 0.45)))
        for i, p in enumerate(peaks):
            x = 12 + i * cell + cell / 2
            bh = p * h * 0.4
            if bh < 1.5:
                cr.arc(x, h / 2, 0.9, 0, math.tau)
            else:
                cr.rectangle(x - cell * 0.25, h / 2 - bh, cell * 0.5, bh * 2)
            cr.fill()

    # ---- prévia ----
    def preview(self, which, btn):
        """Toca os primeiros segundos com o pan; o mesmo botão de novo para."""
        if btn is self.playing_btn:
            self.stop()
            return
        if which == "L" and self.out == "mono":
            which = "mono"
        dst = Path(GLib.get_user_runtime_dir()) / "bergamota-previa.wav"
        af = channel_filter(self.n, self.vl, self.vr, which)
        self.render_preview(channels_cmd(self.src, af, dst, self.PREVIEW_S), dst, btn)

    def save(self):
        self.stop()
        dst = audio_out(self.src, "-mono" if self.out == "mono" else "-estereo")
        self.save_ffmpeg(channels_cmd(self.src, channel_filter(self.n, self.vl, self.vr, self.out), dst), dst)


# ---------------- Bipe ----------------
BLEEP_GUTTER, BLEEP_LAYER, BLEEP_GAP, BLEEP_WAVE, BLEEP_MIN = 52, 44, 8, 96, 0.05


def open_bleeper(src):
    Bleeper(Gio.Application.get_default(), Path(src)).present()


class Bleeper(AudioPreview, PhotoTool, Adw.ApplicationWindow):
    """Bipe: arraste na camada de bipes para marcar trechos; mova o bloco ou as pontas e compare com o original."""

    def __init__(self, app, src):
        super().__init__(application=app, title=f"Bipe — {src.name}", default_width=480)
        load_css(EDITOR_CSS, TRIM_CSS)
        self.src, self.dur, self.rate = src, media_duration(src), audio_rate(src)
        self.bleeps, self.sel, self.tdrag, self.peaks, self.pos, self.fresh = [], None, None, None, 0.0, False
        header = tool_header(self, "Bipe", src.name, "Salvar cópia", self.reset_all)

        self.count = Gtk.Label(xalign=0, margin_top=12, margin_start=14)
        self.count.add_css_class("heading")
        self.area = Gtk.DrawingArea(height_request=BLEEP_LAYER + BLEEP_GAP + BLEEP_WAVE, margin_top=8, margin_start=14,
                                    margin_end=14)
        self.area.set_draw_func(self.draw)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self.on_begin)
        drag.connect("drag-update", lambda _g, dx, _dy: self.on_update(dx))
        drag.connect("drag-end", lambda _g, dx, _dy: self.on_end(dx))
        self.area.add_controller(drag)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", lambda _m, x, y: self.tdrag or self.area.set_cursor_from_name(self.cursor_at(x, y)))
        self.area.add_controller(motion)

        times = Gtk.Box(margin_top=4, margin_start=14 + BLEEP_GUTTER, margin_end=14)
        self.pos_label = Gtk.Label(hexpand=True, xalign=0)
        self.pos_label.add_css_class("timecode")
        self.pos_label.add_css_class("dim-label")
        total = Gtk.Label(label=fmt_tc(self.dur))
        total.add_css_class("timecode")
        total.add_css_class("dim-label")
        times.append(self.pos_label)
        times.append(total)

        hint = Gtk.Label(label="Arraste na camada Bipe para marcar um trecho. Depois arraste o bloco ou as pontas "
                               "para ajustar. Delete remove.", xalign=0, wrap=True, margin_top=12, margin_start=14,
                         margin_end=14)
        hint.add_css_class("dim-label")
        hint.add_css_class("caption")

        self.btn_orig = Gtk.Button(child=Adw.ButtonContent(icon_name="media-playback-start-symbolic", label="Original"))
        self.btn_bleep = Gtk.Button(child=Adw.ButtonContent(icon_name="media-playback-start-symbolic",
                                                            label="Com bipes"))
        self.btn_orig.connect("clicked", lambda b: self.listen(b, False))
        self.btn_bleep.connect("clicked", lambda b: self.listen(b, True))
        prow = Gtk.Box(spacing=8, margin_top=12, margin_bottom=14, margin_start=14, margin_end=14)
        name = Gtk.Label(label="Prévia", xalign=0, hexpand=True)
        name.add_css_class("dim-label")
        for wdg in (name, self.btn_orig, self.btn_bleep):
            prow.append(wdg)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for wdg in (self.count, self.area, times, hint, Gtk.Separator(margin_top=12, margin_start=14, margin_end=14),
                    prow):
            body.append(wdg)
        view = Adw.ToolbarView(content=body)
        view.add_top_bar(header)
        self.toast = Adw.ToastOverlay(child=view)
        self.set_content(self.toast)
        self.connect("close-request", lambda _w: self.stop() or False)
        threading.Thread(target=self.analyze, daemon=True).start()
        self.refresh()

    def analyze(self):
        try:
            peaks = waveform(self.src, WAVE_BARS)
        except subprocess.CalledProcessError:
            return
        GLib.idle_add(lambda: (setattr(self, "peaks", peaks), self.area.queue_draw()) and False)

    def refresh(self):
        n = len(self.bleeps)
        self.count.set_label(f"Bipes  {n}")
        self.save_btn.set_sensitive(n > 0)
        self.btn_bleep.set_sensitive(n > 0)
        self.pos_label.set_label(fmt_tc(self.pos))
        self.area.queue_draw()

    def changed(self):
        self.stop()  # a prévia tocando ficou velha
        self.refresh()

    def reset_all(self):
        self.bleeps, self.sel = [], None
        self.changed()

    def on_key(self, _c, keyval, _code, _state):
        if Gdk.keyval_name(keyval) in ("Delete", "BackSpace") and self.sel is not None:
            del self.bleeps[self.sel]
            self.sel = None
            self.changed()
            return True
        return False

    def on_play_pos(self, t):
        self.pos = t
        self.refresh()

    def listen(self, btn, bleeped):
        if btn is self.playing_btn:
            self.stop()
            return
        if self.pos >= self.dur - 0.1:  # terminou de tocar: recomeça
            self.pos = 0.0
        if bleeped:
            dst = Path(GLib.get_user_runtime_dir()) / "bergamota-previa.wav"
            self.render_preview(bleep_cmd(self.src, self.ranges(), self.dur, self.rate, dst), dst, btn, self.pos)
        else:
            self.stop()
            self.playing_btn = btn
            self.set_btn_icon(btn, "media-playback-stop-symbolic")
            self.start_player(self.src.resolve(), self.pos)

    def ranges(self):
        return sorted((a, b) for a, b in self.bleeps)

    # ---- camada de bipes ----
    def tx(self, t):
        return BLEEP_GUTTER + (self.area.get_width() - BLEEP_GUTTER) * t / self.dur

    def xt(self, x):
        return min(max((x - BLEEP_GUTTER) / max(1, self.area.get_width() - BLEEP_GUTTER), 0), 1) * self.dur

    def hit(self, x, y):
        """(tipo, índice) sob o ponteiro: ponta ou corpo de um bipe, camada vazia ou a onda (seek)."""
        if y > BLEEP_LAYER:
            return "seek", None
        for i in reversed(range(len(self.bleeps))):
            xs, xe = self.tx(self.bleeps[i][0]), self.tx(self.bleeps[i][1])
            if abs(x - xs) <= 8:
                return "t0", i
            if abs(x - xe) <= 8:
                return "t1", i
            if xs < x < xe:
                return "move", i
        return "new", None

    def cursor_at(self, x, y):
        kind = self.hit(x, y)[0]
        return {"t0": "w-resize", "t1": "e-resize", "move": "move", "new": "crosshair"}.get(kind, "pointer")

    def on_begin(self, _g, x, y):
        kind, i = self.hit(x, y)
        if kind == "new":
            self.bleeps.append([self.xt(x), self.xt(x)])
            i = len(self.bleeps) - 1
        self.tdrag = (kind, i, x, *(self.bleeps[i] if i is not None else (0, 0)))
        if i is not None:
            self.sel = i
        if kind == "seek":
            self.seek_to(self.xt(x))
        self.area.grab_focus()
        self.refresh()

    def seek_to(self, t):
        self.stop()
        self.pos = t

    def on_update(self, dx):
        if not self.tdrag:
            return
        kind, i, x, t0, t1 = self.tdrag
        t = self.xt(x + dx)
        if kind == "seek":
            self.seek_to(t)
        elif kind == "new":
            self.bleeps[i] = sorted((t0, t))
        elif kind == "t0":
            self.bleeps[i][0] = min(t, t1 - BLEEP_MIN)
        elif kind == "t1":
            self.bleeps[i][1] = max(t, t0 + BLEEP_MIN)
        else:
            d = min(max(t - self.xt(x), -t0), self.dur - t1)
            self.bleeps[i] = [t0 + d, t1 + d]
        self.refresh()

    def on_end(self, _dx):
        if self.tdrag and self.tdrag[0] == "new":
            a, b = self.bleeps[self.tdrag[1]]
            if b - a < BLEEP_MIN:  # clique sem arrastar: não cria bipe
                self.bleeps.pop(self.tdrag[1])
                self.sel = None
        self.tdrag = None
        self.changed()

    def draw(self, _a, cr, w, h):
        dark, accent = system_colors()
        fg = (0.8, 0.8, 0.82) if dark else (0.3, 0.3, 0.33)
        G = BLEEP_GUTTER
        track = (1, 1, 1, 0.06) if dark else (0, 0, 0, 0.05)
        wy = BLEEP_LAYER + BLEEP_GAP
        for y, hgt, name in ((0, BLEEP_LAYER, "Bipe"), (wy, BLEEP_WAVE, "Áudio")):
            cr.set_source_rgba(*track)
            rounded_rect(cr, G, y, w - G, hgt, 8)
            cr.fill()
            show_centered(cr, label_layout(cr, name, 9.5, False), G / 2 - 4, y + hgt / 2, fg)
        if self.peaks:
            cell = (w - G - 8) / len(self.peaks)
            cr.set_source_rgb(*accent)
            for i, p in enumerate(self.peaks):
                x = G + 4 + i * cell + cell / 2
                bh = p * BLEEP_WAVE * 0.42
                if bh < 2:
                    cr.arc(x, wy + BLEEP_WAVE / 2, 1, 0, math.tau)
                else:
                    cr.rectangle(x - cell * 0.28, wy + BLEEP_WAVE / 2 - bh, cell * 0.56, bh * 2)
                cr.fill()
        for i, (a, b) in enumerate(self.bleeps):
            xs, xe = self.tx(a), self.tx(b)
            cr.set_source_rgba(*accent, 0.95 if i == self.sel else 0.55)
            rounded_rect(cr, xs, 0, max(6, xe - xs), BLEEP_LAYER, 8)
            cr.fill()
            cr.set_source_rgb(1, 1, 1)
            if xe - xs > 24:
                for gx in (xs + 7, xe - 7):
                    rounded_rect(cr, gx - 1.5, 12, 3, BLEEP_LAYER - 24, 1.5)
                    cr.fill()
            if xe - xs > 64:
                show_centered(cr, label_layout(cr, "Bipe", 10, True), (xs + xe) / 2, BLEEP_LAYER / 2, (1, 1, 1))
            cr.set_source_rgba(*accent, 0.25)  # o trecho que some, marcado na onda
            cr.rectangle(xs, wy, xe - xs, BLEEP_WAVE)
            cr.fill()
        px = self.tx(self.pos)
        cr.set_source_rgb(*fg)
        cr.rectangle(px - 1, 0, 2, h)
        cr.fill()

    def save(self):
        self.stop()
        dst = audio_out(self.src, "-bipe")
        self.save_ffmpeg(bleep_cmd(self.src, self.ranges(), self.dur, self.rate, dst), dst)


# ---------------- Unir vídeos ----------------
def open_join(files):
    VideoJoin(Gio.Application.get_default(), [Path(f) for f in files]).present()


class FileOrder(PhotoTool, Adw.ApplicationWindow):
    """Lista numerada para ordenar arquivos antes de juntar; começa na ordem em que foram soltos.
    A classe define row_subtitle(f) e row_prefix(f) -> widget."""

    def setup_list(self, title, subtitle, save_label, files):
        self.files, self.order, self.rows = files, list(files), []
        header = tool_header(self, title, subtitle, save_label, self.reset_all)
        self.group = Adw.PreferencesGroup(title=self.list_title)
        page = Gtk.ScrolledWindow(child=Adw.Clamp(child=self.group, margin_top=12, margin_bottom=12, margin_start=12,
                                                  margin_end=12, maximum_size=700),
                                  hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        view = Adw.ToolbarView(content=page)
        view.add_top_bar(header)
        self.toast = Adw.ToastOverlay(child=view)
        self.set_content(self.toast)
        self.fill()

    def fill(self):
        for r in self.rows:
            self.group.remove(r)
        self.rows = []
        self.describe()
        for pos, f in enumerate(self.order):
            row = Adw.ActionRow(title=f.name, title_lines=1, subtitle=self.row_subtitle(f))
            row.add_prefix(self.row_prefix(f))  # add_prefix põe à esquerda do anterior: miniatura antes, número depois
            num = Gtk.Label(label=str(pos + 1), width_chars=2)
            num.add_css_class("dim-label")
            row.add_prefix(num)
            for icon, d, ok in (("go-up-symbolic", -1, pos > 0), ("go-down-symbolic", 1, pos < len(self.order) - 1)):
                b = Gtk.Button(icon_name=icon, valign=Gtk.Align.CENTER, sensitive=ok)
                b.add_css_class("flat")
                b.connect("clicked", lambda _b, pos=pos, d=d: self.move(pos, d))
                row.add_suffix(b)
            self.group.add(row)
            self.rows.append(row)

    def describe(self):
        pass

    def move(self, pos, d):
        o = self.order
        o[pos], o[pos + d] = o[pos + d], o[pos]
        self.fill()

    def reset_all(self):
        self.order = list(self.files)
        self.fill()


class VideoJoin(FileOrder):
    """Unir vídeos: ordena os clipes (MP4, MOV, MKV…) e junta num MP4 com a tela do primeiro."""
    list_title = "Ordem de reprodução"

    def __init__(self, app, files):
        super().__init__(application=app, title="Unir vídeos", default_width=400, default_height=400)
        self.src = files[0].parent / "videos-unidos.mp4"  # nome do arquivo salvo
        self.info = {f: (*probe_video(f), has_audio(f)) for f in files}
        self.thumbs = {}
        self.setup_list("Unir vídeos", f"{len(files)} vídeos", "Unir vídeos", files)
        threading.Thread(target=self.load_thumbs, daemon=True).start()

    def load_thumbs(self):
        for f in self.files:
            try:
                from PIL import ImageOps
                # tamanho fixo com tarja: o Gtk.Picture pede o tamanho natural da imagem e esticaria a linha
                im = ImageOps.pad(video_frame(f, min(1, self.info[f][3] / 2), (128, -2)).convert("RGB"), (64, 40),
                                  color=(22, 22, 24))
            except subprocess.CalledProcessError:
                continue
            GLib.idle_add(lambda f=f, im=im: (self.thumbs.__setitem__(f, pil_texture(im)), self.fill()) and False)

    def describe(self):
        W, H, fps = self.info[self.order[0]][:3]
        self.group.set_description(f"Sai um MP4 de {W // 2 * 2}×{H // 2 * 2}, {fps:g} fps — a tela do primeiro vídeo. "
                                   "Os outros cabem inteiros, com tarja se a proporção for diferente.")

    def row_subtitle(self, f):
        w, h, _fps, dur, audio = self.info[f]
        return f"{fmt_tc(dur)} · {w}×{h}" + ("" if audio else " · sem som")

    def row_prefix(self, f):
        return Gtk.Picture(paintable=self.thumbs.get(f), width_request=64, height_request=40, valign=Gtk.Align.CENTER)

    def save(self):
        W, H, fps = self.info[self.order[0]][:3]
        clips = [(f, self.info[f][3], self.info[f][4]) for f in self.order]
        dst = out_path(self.src, "mp4")
        self.save_ffmpeg(join_cmd(clips, W, H, fps, dst), dst)


# ---------------- Unir PDFs ----------------
def open_pdf_merge(files):
    PdfMerge(Gio.Application.get_default(), [Path(f) for f in files]).present()


class PdfMerge(FileOrder):
    """Unir PDFs: ordena os documentos e junta num só."""
    list_title = "Ordem dos documentos"

    def __init__(self, app, files):
        super().__init__(application=app, title="Unir PDFs", default_width=400, default_height=400)
        self.src = files[0].parent / "pdfs-unidos.pdf"  # nome do arquivo salvo
        self.pages = {f: pdf_pages(f) for f in files}
        self.setup_list("Unir PDFs", f"{len(files)} documentos", "Unir PDFs", files)

    def describe(self):
        known = [self.pages[f] for f in self.order]
        self.group.set_description(f"{sum(known)} páginas no total." if all(known) else None)

    def row_subtitle(self, f):
        n = self.pages[f]
        return f"{n} página" + ("" if n == 1 else "s") if n else "Não deu para ler este PDF"

    def row_prefix(self, f):
        return icon_widget("pdf")

    def save(self):
        dst = out_path(self.src, "pdf")
        self.save_ffmpeg(["pdfunite", *map(str, self.order), str(dst)], dst)


# ---------------- Organizar PDF ----------------
ORG_CSS = b"""
.org-card { border-radius: 10px; background: alpha(currentColor, 0.06); padding: 6px; }
.org-num { background: @accent_bg_color; color: @accent_fg_color; border-radius: 99px; min-width: 22px; min-height: 22px;
           font-weight: bold; margin: 4px; }
.org-btn { min-width: 26px; min-height: 26px; padding: 0; margin: 3px; background: rgba(0,0,0,.65); color: white; }
"""
ORG_THUMB = (130, 170)


def open_organizer(src):
    try:
        Organizer(Gio.Application.get_default(), Path(src)).present()
    except ValueError:  # corrompido ou protegido por senha
        notify(f"Não deu para abrir {Path(src).name}")


class Organizer(PhotoTool, Adw.ApplicationWindow):
    """Organizar PDF: arraste páginas para reordenar; gire, duplique ou remova cada uma."""

    def __init__(self, app, src):
        super().__init__(application=app, title=f"Organizar PDF — {src.name}", default_width=540, default_height=640)
        load_css(ORG_CSS)
        self.src = src
        self.total = len(pdf_sizes(src))
        self.original = [(i, 0) for i in range(self.total)]
        self.pages, self.thumbs, self.dragged = list(self.original), {}, None
        header = tool_header(self, "Organizar PDF", src.name, "Salvar PDF", self.reset_all)

        self.count = Gtk.Label(xalign=0, hexpand=True, margin_top=12, margin_start=14, margin_end=14)
        self.count.add_css_class("dim-label")
        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, min_children_per_line=2,
                                max_children_per_line=12, column_spacing=10, row_spacing=10, valign=Gtk.Align.START,
                                margin_top=8, margin_bottom=8, margin_start=12, margin_end=12)
        scroll = Gtk.ScrolledWindow(child=self.flow, hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.same_width = Gtk.CheckButton(label="Deixar todas as páginas com a mesma largura", margin_top=8,
                                          margin_start=10)
        note = Gtk.Label(label="As páginas diminuem na proporção até a largura da mais estreita; a altura pode variar.",
                         xalign=0, wrap=True, margin_start=14, margin_end=14, margin_bottom=12)
        note.add_css_class("dim-label")
        note.add_css_class("caption")
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for wdg in (self.count, scroll, Gtk.Separator(), self.same_width, note):
            body.append(wdg)
        view = Adw.ToolbarView(content=body)
        view.add_top_bar(header)
        self.toast = Adw.ToastOverlay(child=view)
        self.set_content(self.toast)
        self.refresh()
        threading.Thread(target=self.load_thumbs, daemon=True).start()

    def load_thumbs(self):
        import tempfile
        from PIL import Image, ImageOps
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["pdftoppm", "-scale-to", str(ORG_THUMB[1] * 2), "-png", str(self.src), f"{tmp}/p"],
                           capture_output=True)
            files = sorted(Path(tmp).glob("p*.png"), key=lambda f: int(f.stem.rsplit("-", 1)[1]))
            thumbs = {i: ImageOps.contain(Image.open(f).convert("RGB"), ORG_THUMB) for i, f in enumerate(files)}
        GLib.idle_add(lambda: (setattr(self, "thumbs", thumbs), self.refresh()) and False)

    # ---- estado ----
    def reset_all(self):
        self.pages = list(self.original)
        self.refresh()

    def rotate(self, pos):
        i, turn = self.pages[pos]
        self.pages[pos] = (i, (turn + 90) % 360)
        self.refresh()

    def duplicate(self, pos):
        self.pages.insert(pos + 1, self.pages[pos])
        self.refresh()

    def remove(self, pos):
        del self.pages[pos]
        self.refresh()

    def move(self, a, b):
        self.pages.insert(b, self.pages.pop(a))
        self.refresh()

    def thumb_texture(self, i, turn):
        im = self.thumbs.get(i)
        if im is None:
            return None
        return pil_texture(im.rotate(-turn, expand=True) if turn else im)

    def overlay_btn(self, icon, tip, fn, halign, valign, ok=True):
        b = Gtk.Button(icon_name=icon, tooltip_text=tip, halign=halign, valign=valign, sensitive=ok)
        b.add_css_class("circular")
        b.add_css_class("org-btn")
        b.connect("clicked", lambda _b: fn())
        return b

    def card(self, pos, i, turn):
        pic = Gtk.Picture(paintable=self.thumb_texture(i, turn), halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        box = Gtk.Overlay(child=pic, width_request=ORG_THUMB[0], height_request=ORG_THUMB[1])
        box.add_css_class("org-card")
        num = Gtk.Label(label=str(pos + 1), halign=Gtk.Align.START, valign=Gtk.Align.START,
                        tooltip_text=f"Página {i + 1} do original")
        num.add_css_class("org-num")
        box.add_overlay(num)
        s, e = Gtk.Align.START, Gtk.Align.END
        box.add_overlay(self.overlay_btn("object-rotate-right-symbolic", "Girar", lambda: self.rotate(pos), e, s))
        box.add_overlay(self.overlay_btn("edit-copy-symbolic", "Duplicar", lambda: self.duplicate(pos), s, e))
        box.add_overlay(self.overlay_btn("user-trash-symbolic", "Remover", lambda: self.remove(pos), e, e,
                                         len(self.pages) > 1))
        source = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        source.connect("prepare", self.drag_prepare, pos)
        box.add_controller(source)
        target = Gtk.DropTarget.new(str, Gdk.DragAction.MOVE)
        target.connect("drop", self.drag_drop, pos)
        box.add_controller(target)
        return box

    def drag_prepare(self, source, _x, _y, pos):
        self.dragged = pos
        tex = self.thumb_texture(*self.pages[pos])
        if tex:
            source.set_icon(tex, 0, 0)
        return Gdk.ContentProvider.new_for_value("bergamota-pagina")

    def drag_drop(self, _t, _value, _x, _y, pos):
        if self.dragged is None or self.dragged == pos:
            return False
        GLib.idle_add(self.move, self.dragged, pos)  # refresh destrói este cartão: espera o drop terminar
        self.dragged = None
        return True

    def refresh(self):
        # ponytail: recria todos os cartões a cada mudança; lento só em PDFs de centenas de páginas
        self.flow.remove_all()
        for pos, (i, turn) in enumerate(self.pages):
            self.flow.append(self.card(pos, i, turn))
        n = len(self.pages)
        self.count.set_label(f"{n} página" + ("" if n == 1 else "s") + " · arraste para mudar a ordem")

    def save(self):
        dst = out_path(self.src, "pdf", "-organizado")
        pages, same = list(self.pages), self.same_width.get_active()
        self.save_btn.set_sensitive(False)
        self.save_btn.set_label("Salvando…")

        def work():
            try:
                organize_pdf(self.src, pages, same, dst)
                msg = f"Salvo: {dst.name}"
            except subprocess.CalledProcessError as ex:
                msg = f"Erro ao salvar: {(ex.stderr or b'').decode(errors='ignore').strip()[-120:] or ex}"
            except (OSError, ValueError, RuntimeError) as ex:
                msg = f"Erro ao salvar: {ex}"
            GLib.idle_add(self._saved, msg)
        threading.Thread(target=work, daemon=True).start()


# ---------------- Comprimir ----------------
COMPRESS_PRESETS = (("Equilibrado", "Arquivos menores com pouca perda de qualidade."),
                    ("Forte", "Os menores arquivos, com perda de qualidade visível."))
COMPRESS_SCALES = (("Original", 1), ("75%", 0.75), ("50%", 0.5), ("25%", 0.25))
TARGET_UNITS = ("MB", "% menor")


def open_compressor(files):
    Compressor(Gio.Application.get_default(), [Path(f) for f in files]).present()


class Compressor(PhotoTool, Adw.ApplicationWindow):
    """Comprimir: qualquer arquivo, sem tocar no original; predefinição, tamanho da imagem e tamanho alvo."""

    def __init__(self, app, files):
        super().__init__(application=app, title="Comprimir", default_width=400)
        load_css(EDITOR_CSS)
        self.files, self.preset, self.scale = files, 0, 1
        self.sizes = {f: f.stat().st_size for f in files}
        kinds = {file_kind(f) for f in files}
        many = len(files) > 1
        header = tool_header(self, "Comprimir", f"{len(files)} arquivos" if many else files[0].name, "Comprimir",
                             self.reset_all)

        name = Gtk.Label(label=f"{len(files)} arquivos" if many else files[0].name, xalign=0, ellipsize=3,
                         margin_top=12, margin_start=14, margin_end=14)
        name.add_css_class("dim-label")
        total = Gtk.Label(label=f"Tamanho original: {fmt_size(sum(self.sizes.values()))}", xalign=0, margin_top=8,
                          margin_start=14)
        total.add_css_class("heading")
        title = Gtk.Label(label="Predefinição", xalign=0, margin_top=14, margin_start=14)
        title.add_css_class("title-4")
        presets = Gtk.Box(halign=Gtk.Align.START, margin_top=8, margin_start=14)
        presets.add_css_class("linked")
        first = None
        for i, (label, _desc) in enumerate(COMPRESS_PRESETS):
            b = Gtk.ToggleButton(label=label, active=i == 0, group=first)
            first = first or b
            b.connect("toggled", lambda b, i=i: b.get_active() and self.set_preset(i))
            presets.append(b)
        self.first_preset = first
        self.preset_note = Gtk.Label(xalign=0, margin_top=6, margin_start=14)
        self.preset_note.add_css_class("dim-label")
        self.preset_note.add_css_class("caption")

        self.scale_row = Adw.ComboRow(title="Tamanho da imagem", visible=bool(kinds & {"image", "gif"}),
                                      model=Gtk.StringList.new([n for n, _k in COMPRESS_SCALES]))
        self.scale_row.connect("notify::selected", lambda r, _p: setattr(self, "scale",
                                                                         COMPRESS_SCALES[r.get_selected()][1]))
        self.target_row = Adw.SwitchRow(title="Comprimir para um tamanho alvo",
                                        visible=bool(kinds - {"other", "gif"}))
        self.target_row.connect("notify::active", lambda *_: self.sync_target())
        self.spin = Gtk.SpinButton.new_with_range(1, 100000, 1)
        self.spin.set_digits(1)
        self.spin.set_valign(Gtk.Align.CENTER)
        self.spin.set_width_chars(7)
        self.units = Gtk.DropDown(model=Gtk.StringList.new(TARGET_UNITS[1:] if many else TARGET_UNITS),
                                  valign=Gtk.Align.CENTER)
        self.units.connect("notify::selected", lambda *_: self.sync_unit())
        self.value_row = Adw.ActionRow(title="Tamanho desejado" if not many else "Reduzir cada arquivo em")
        self.value_row.add_suffix(self.spin)
        self.value_row.add_suffix(self.units)
        self.result = Gtk.Label(xalign=0, wrap=True, margin_top=10, margin_start=14, margin_end=14, margin_bottom=12)
        self.result.add_css_class("dim-label")
        rows = Adw.Clamp(child=boxed_list([self.scale_row, self.target_row, self.value_row]), margin_top=12,
                         margin_start=12, margin_end=12, maximum_size=900)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for wdg in (name, total, title, presets, self.preset_note, rows, self.result):
            body.append(wdg)
        view = Adw.ToolbarView(content=body)
        view.add_top_bar(header)
        self.toast = Adw.ToastOverlay(child=view)
        self.set_content(self.toast)
        self.set_preset(0)
        self.sync_unit()
        self.sync_target()

    def set_preset(self, i):
        self.preset = i
        self.preset_note.set_label(COMPRESS_PRESETS[i][1])

    def is_percent(self):
        return len(self.files) > 1 or self.units.get_selected() == 1

    def sync_unit(self):
        if self.is_percent():
            self.spin.set_range(1, 95)
            self.spin.set_value(50)
        else:
            mb = sum(self.sizes.values()) / 1e6
            self.spin.set_range(0.1, max(mb, 0.1))
            self.spin.set_increments(0.1, 1)
            self.spin.set_value(max(round(mb / 2, 1), 0.1))

    def sync_target(self):
        self.value_row.set_visible(self.target_row.get_visible() and self.target_row.get_active())

    def reset_all(self):
        self.first_preset.set_active(True)
        self.scale_row.set_selected(0)
        self.target_row.set_active(False)
        self.result.set_label("")

    def target_for(self, f):
        if not self.target_row.get_active():
            return None
        x = self.spin.get_value()
        return int(self.sizes[f] * (1 - x / 100)) if self.is_percent() else int(x * 1e6)

    def save(self):
        self.save_btn.set_sensitive(False)
        jobs = [(f, self.target_for(f)) for f in self.files]
        preset, scale = self.preset, self.scale

        def work():
            done, same, errs, before, after, over = [], [], [], 0, 0, False
            for n, (f, target) in enumerate(jobs, 1):
                GLib.idle_add(self.save_btn.set_label, f"Comprimindo {n}/{len(jobs)}…" if len(jobs) > 1
                              else "Comprimindo…")
                try:
                    dst = compress_file(f, preset, scale, target)
                except (subprocess.CalledProcessError, OSError, ValueError, RuntimeError) as ex:
                    errs.append(f"{f.name}: {str(ex)[:100]}")
                    continue
                if dst is None:
                    same.append(f.name)
                    continue
                done.append(dst)
                before += self.sizes[f]
                after += dst.stat().st_size
                over = over or bool(target and dst.stat().st_size > target * 1.1)
            GLib.idle_add(self.finished, done, same, errs, before, after, over)
        threading.Thread(target=work, daemon=True).start()

    def finished(self, done, same, errs, before, after, over):
        lines = []
        if done:
            lines.append(f"{fmt_size(before)} → {fmt_size(after)} (−{(1 - after / before) * 100:.0f}%)")
            if over:
                lines.append("Não deu para chegar ao tamanho alvo; este é o menor resultado possível aqui.")
        if same:
            lines.append("Já estava compacto, nada foi criado: " + ", ".join(same))
        lines += errs
        self.result.set_label("\n".join(lines))
        self._saved(f"Salvo: {done[0].name}" if len(done) == 1 else f"{len(done)} arquivos salvos" if done
                    else "Nenhum arquivo novo")


# ---------------- Metadados ----------------
META_NOTE = "Remover tudo também apaga dados ocultos de câmera e processamento; efeitos de HDR e retrato podem mudar."


def open_metadata(src):
    try:
        Metadata(Gio.Application.get_default(), Path(src)).present()
    except FileNotFoundError:
        notify("Falta o exiftool: sudo apt install libimage-exiftool-perl")
    except (subprocess.CalledProcessError, ValueError, IndexError):
        notify(f"Não deu para ler os metadados de {Path(src).name}")


def meta_label(tag):
    """'ExifImageWidth' vira 'Exif Image Width'."""
    import re
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", tag.replace("_", " "))
    return words.capitalize() if tag.islower() else words


class Metadata(PhotoTool, Adw.ApplicationWindow):
    """Metadados: procura, edita ou remove campos, agrupados por origem; salva sempre numa cópia."""

    def __init__(self, app, src):
        super().__init__(application=app, title=f"Metadados — {src.name}", default_width=480, default_height=620)
        self.src = src
        self.route = meta_route(src)
        self.fields = read_metadata(src)
        self.tags = exiftool_list("-listw") if self.route == "exif" else None
        self.values, self.entries, self.removed, self.rows = {}, {}, set(), []
        header = tool_header(self, "Metadados", src.name, "Salvar cópia", self.reset_all)

        self.search = Gtk.SearchEntry(placeholder_text="Procurar metadados", margin_top=12, margin_start=14,
                                      margin_end=14)
        self.search.connect("search-changed", lambda *_: self.apply_filter())
        self.page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin_top=12, margin_bottom=12,
                            margin_start=12, margin_end=12)
        scroll = Gtk.ScrolledWindow(child=self.page, hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.fill()

        self.note = Gtk.Label(label=META_NOTE if self.route else "Este formato não permite gravar metadados: "
                              "dá só para ver.", xalign=0, wrap=True, margin_top=8, margin_start=14, margin_end=14)
        self.note.add_css_class("dim-label")
        self.note.add_css_class("caption")
        strip = Gtk.Button(label="Remover tudo", halign=Gtk.Align.END, sensitive=self.route is not None, margin_top=8,
                           margin_bottom=12, margin_end=14)
        strip.add_css_class("destructive-action")
        strip.connect("clicked", lambda _b: self.remove_all())
        self.strip_btn = strip
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for wdg in (self.search, scroll, Gtk.Separator(), self.note, strip):
            body.append(wdg)
        view = Adw.ToolbarView(content=body)
        view.add_top_bar(header)
        self.toast = Adw.ToastOverlay(child=view)
        self.set_content(self.toast)
        self.sync_save()

    def editable(self, group, tag, value):
        if self.route is None or group in META_READONLY or not isinstance(value, (str, int, float)):
            return False
        if isinstance(value, str) and value.startswith("(Binary data"):
            return False
        return self.route == "ffmpeg" or tag in self.tags

    def fill(self):
        groups = {}
        for group, tag, value in self.fields:
            groups.setdefault(group, []).append((tag, value))
        # etiquetas gravadas primeiro; dados do próprio arquivo no fim
        for group in sorted(groups, key=lambda g: (g in META_READONLY, g != META_TAGS_GROUP, g)):
            box = Adw.PreferencesGroup(title=group)
            self.page.append(box)
            for tag, value in groups[group]:
                key, text = f"{group}:{tag}", str(value)
                self.values[key] = text
                if self.editable(group, tag, value):
                    row = Adw.EntryRow(title=meta_label(tag), text=text)
                    row.connect("changed", lambda *_: self.sync_save())
                    self.entries[key] = row
                else:
                    row = Adw.ActionRow(title=meta_label(tag), subtitle=GLib.markup_escape_text(text[:200]),
                                        subtitle_selectable=True)
                    row.add_css_class("property")
                if key in self.entries or (self.route and group not in META_READONLY):
                    b = Gtk.Button(icon_name="list-remove-symbolic", tooltip_text="Remover campo",
                                   valign=Gtk.Align.CENTER)
                    b.add_css_class("flat")
                    b.add_css_class("circular")
                    b.connect("clicked", lambda b, key=key, row=row: self.toggle(key, row, b))
                    row.add_suffix(b)
                box.add(row)
                self.rows.append((box, row, f"{group} {tag} {meta_label(tag)} {text}".lower()))

    def toggle(self, key, row, btn):
        gone = key not in self.removed
        (self.removed.add if gone else self.removed.discard)(key)
        row.set_opacity(0.4 if gone else 1)
        btn.set_icon_name("edit-undo-symbolic" if gone else "list-remove-symbolic")
        btn.set_tooltip_text("Manter campo" if gone else "Remover campo")
        self.sync_save()

    def apply_filter(self):
        q = self.search.get_text().lower().strip()
        seen = set()
        for box, row, text in self.rows:
            hit = q in text
            row.set_visible(hit)
            if hit:
                seen.add(box)
        for box in {r[0] for r in self.rows}:
            box.set_visible(box in seen)

    def edits(self):
        return {k: e.get_text() for k, e in self.entries.items()
                if k not in self.removed and e.get_text() != self.values[k]}

    def sync_save(self):
        self.save_btn.set_sensitive(self.route is not None and bool(self.removed or self.edits()))

    def reset_all(self):
        while self.page.get_first_child():
            self.page.remove(self.page.get_first_child())
        self.values, self.entries, self.removed, self.rows = {}, {}, set(), []
        self.fill()
        self.apply_filter()
        self.sync_save()

    def run_save(self, suffix, job):
        dst = out_path(self.src, self.src.suffix.lstrip(".").lower(), suffix)
        self.save_btn.set_sensitive(False)
        self.save_btn.set_label("Salvando…")

        def work():
            try:
                job(dst)
                msg = f"Salvo: {dst.name}"
            except subprocess.CalledProcessError as ex:
                dst.unlink(missing_ok=True)
                msg = f"Erro ao salvar: {(ex.stderr or b'').decode(errors='ignore').strip()[-140:] or ex}"
            except (OSError, RuntimeError) as ex:
                msg = f"Erro ao salvar: {ex}"
            GLib.idle_add(self.saved, msg)
        threading.Thread(target=work, daemon=True).start()

    def saved(self, msg):
        self._saved(msg)
        self.sync_save()

    def save(self):
        edits, removed = self.edits(), set(self.removed)
        self.run_save("-metadados", lambda dst: write_metadata(self.src, edits, removed, dst))

    def remove_all(self):
        self.run_save("-sem-metadados", lambda dst: strip_metadata(self.src, dst))


# ---------------- Dividir PDF ----------------
SPLIT_MODES = ("Uma página por arquivo", "A cada N páginas", "Intervalos")


def open_splitter(src):
    try:
        Splitter(Gio.Application.get_default(), Path(src)).present()
    except ValueError:  # corrompido ou protegido por senha
        notify(f"Não deu para abrir {Path(src).name}")


class Splitter(PhotoTool, Adw.ApplicationWindow):
    """Dividir PDF: uma página por arquivo, a cada N páginas ou por intervalos escritos à mão."""

    def __init__(self, app, src):
        super().__init__(application=app, title=f"Dividir PDF — {src.name}", default_width=420)
        self.src, self.total = src, len(pdf_sizes(src))
        header = tool_header(self, "Dividir PDF", src.name, "Dividir", self.reset_all)
        self.mode_row = Adw.ComboRow(title="Como dividir", model=Gtk.StringList.new(SPLIT_MODES))
        self.mode_row.connect("notify::selected", lambda *_: self.sync())
        self.every_row = Adw.SpinRow.new_with_range(1, max(self.total, 1), 1)
        self.every_row.set_title("Páginas por arquivo")
        self.every_row.set_value(min(2, self.total))
        self.every_row.connect("notify::value", lambda *_: self.sync())
        self.ranges_row = Adw.EntryRow(title="Intervalos, como 1-3, 5, 8-")
        self.ranges_row.connect("changed", lambda *_: self.sync())
        rows = Adw.Clamp(child=boxed_list([self.mode_row, self.every_row, self.ranges_row]), margin_top=12,
                         margin_start=12, margin_end=12, maximum_size=900)
        self.info = Gtk.Label(xalign=0, wrap=True, margin_top=10, margin_bottom=14, margin_start=14, margin_end=14)
        self.info.add_css_class("dim-label")
        count = Gtk.Label(label=f"{self.total} página" + ("" if self.total == 1 else "s"), xalign=0, margin_top=12,
                          margin_start=14)
        count.add_css_class("heading")
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for wdg in (count, rows, self.info):
            body.append(wdg)
        view = Adw.ToolbarView(content=body)
        view.add_top_bar(header)
        self.toast = Adw.ToastOverlay(child=view)
        self.set_content(self.toast)
        self.sync()

    def groups(self):
        mode = self.mode_row.get_selected()
        if mode == 2:
            return page_ranges(self.ranges_row.get_text(), self.total)
        return split_groups(self.total, 1 if mode == 0 else int(self.every_row.get_value()))

    def sync(self):
        mode = self.mode_row.get_selected()
        self.every_row.set_visible(mode == 1)
        self.ranges_row.set_visible(mode == 2)
        try:
            n = len(self.groups())
            self.info.set_label(f"Vai gerar {n} PDF" + ("" if n == 1 else "s") + " numa pasta nova ao lado do original.")
            self.save_btn.set_sensitive(True)
        except ValueError as ex:
            self.info.set_label(f"{str(ex)[0].upper()}{str(ex)[1:]}")
            self.save_btn.set_sensitive(False)

    def reset_all(self):
        self.mode_row.set_selected(0)
        self.ranges_row.set_text("")

    def save(self):
        groups = self.groups()
        self.save_btn.set_sensitive(False)
        self.save_btn.set_label("Dividindo…")

        def work():
            try:
                msg = f"Salvo em {split_pdf(self.src, groups).name}"
            except (subprocess.CalledProcessError, OSError, RuntimeError) as ex:
                msg = f"Erro ao dividir: {str(ex)[:120]}"
            GLib.idle_add(self.done, msg)
        threading.Thread(target=work, daemon=True).start()

    def done(self, msg):
        self._saved(msg)
        self.sync()


# ---------------- Ajustes ----------------
class Settings(Adw.ApplicationWindow):
    """Ajustes: qual tecla abre cada modo da roda e comandos próprios na roda de Ações."""

    def __init__(self, app):
        super().__init__(application=app, title="Ajustes", default_width=440, default_height=520)
        self.names = list(KEY_NAMES)
        self.syncing = False
        self.rows = {}
        self.command_rows = []
        for mode, (title, _section) in MODES.items():
            row = Adw.ComboRow(title=title, model=Gtk.StringList.new(list(KEY_NAMES.values())))
            row.connect("notify::selected", lambda r, _p, mode=mode: self.pick(mode, r.get_selected()))
            self.rows[mode] = row
        group = Adw.PreferencesGroup(title="Teclas da roda", description="Durante um arrasto, toque a tecla para abrir "
                                     "o modo. Escolher uma tecla já usada troca as duas.")
        for row in self.rows.values():
            group.add(row)
        restore = Gtk.Button(label="Restaurar padrão", halign=Gtk.Align.END)
        restore.connect("clicked", lambda _b: self.apply(dict(DEFAULT_KEYS)))
        add = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Adicionar comando", valign=Gtk.Align.CENTER)
        add.add_css_class("flat")
        add.connect("clicked", lambda _b: self.edit_command(None))
        self.commands = Adw.PreferencesGroup(title="Comandos personalizados", header_suffix=add,
                                             description="Aparecem na roda de Ações, com contorno em vez de cor.")
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, margin_top=12, margin_bottom=12,
                       margin_start=12, margin_end=12)
        for wdg in (group, restore, self.commands):
            body.append(wdg)
        view = Adw.ToolbarView(content=Gtk.ScrolledWindow(child=body, hscrollbar_policy=Gtk.PolicyType.NEVER))
        view.add_top_bar(Adw.HeaderBar())
        self.set_content(view)
        self.show_keys(load_keys())
        self.fill_commands()

    def show_keys(self, keys):
        self.syncing = True  # mudar a linha daqui não é escolha do usuário
        for mode, row in self.rows.items():
            row.set_selected(self.names.index(keys[mode]))
        self.syncing = False

    def pick(self, mode, i):
        if not self.syncing:
            self.apply(assign_key(load_keys(), mode, self.names[i]))

    def apply(self, keys):
        save_keys(keys)
        self.show_keys(keys)

    def fill_commands(self):
        for row in self.command_rows:
            self.commands.remove(row)
        self.command_rows = []
        for i, c in enumerate(load_commands()):
            row = Adw.ActionRow(title=GLib.markup_escape_text(c["name"]), title_lines=1, subtitle_lines=1,
                                subtitle=GLib.markup_escape_text(c["command"]), activatable=True)
            row.add_prefix(icon_widget(c["icon"]))
            row.connect("activated", lambda _r, i=i: self.edit_command(i))
            trash = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Remover", valign=Gtk.Align.CENTER)
            trash.add_css_class("flat")
            trash.connect("clicked", lambda _b, i=i: self.delete_command(i))
            row.add_suffix(trash)
            self.commands.add(row)
            self.command_rows.append(row)

    def edit_command(self, i):
        commands = load_commands()
        CommandEditor(self, None if i is None else commands[i], lambda c: self.store_command(i, c)).present()

    def store_command(self, i, command):
        commands = load_commands()
        if i is None:
            commands.append(command)
        else:
            commands[i] = command
        save_commands(commands)
        self.fill_commands()

    def delete_command(self, i):
        commands = load_commands()
        del commands[i]
        save_commands(commands)
        self.fill_commands()


class CommandEditor(Adw.Window):
    """Nome, ícone e comando de uma ação personalizada."""

    def __init__(self, parent, command, on_save):
        super().__init__(transient_for=parent, modal=True, title="Comando", default_width=420)
        self.on_save = on_save
        c = command or {"name": "", "icon": "terminal", "command": "", "terminal": False}
        self.icon = c["icon"]
        self.name_row = Adw.EntryRow(title="Nome", text=c["name"])
        self.cmd_row = Adw.EntryRow(title="Comando", text=c["command"])
        pick = Gtk.Button(icon_name="document-open-symbolic", tooltip_text="Escolher programa", valign=Gtk.Align.CENTER)
        pick.add_css_class("flat")
        pick.connect("clicked", lambda _b: self.pick_program())
        self.cmd_row.add_suffix(pick)
        self.term_row = Adw.SwitchRow(title="Abrir no terminal", active=c["terminal"])
        for row in (self.name_row, self.cmd_row):
            row.connect("changed", lambda *_: self.sync())
        form = Adw.PreferencesGroup()
        for row in (self.name_row, self.cmd_row, self.term_row):
            form.add(row)
        hint = Gtk.Label(label="Use {arquivo} para o caminho completo, {nome} para o nome e {pasta} para a pasta, sem "
                               "aspas em volta. Vários arquivos rodam um por vez.",
                         xalign=0, wrap=True)
        hint.add_css_class("dim-label")
        hint.add_css_class("caption")
        icons = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=8, min_children_per_line=8,
                            homogeneous=True, column_spacing=4, row_spacing=4)
        first = None
        for kind in COMMAND_ICONS:
            b = Gtk.ToggleButton(child=icon_widget(kind), group=first, active=kind == self.icon, tooltip_text=kind)
            b.add_css_class("flat")
            first = first or b
            b.connect("toggled", lambda b, kind=kind: b.get_active() and setattr(self, "icon", kind))
            icons.append(b)
        label = Gtk.Label(label="Ícone", xalign=0)
        label.add_css_class("heading")
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=12, margin_bottom=14,
                       margin_start=14, margin_end=14)
        for wdg in (form, hint, label, icons):
            body.append(wdg)
        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        cancel = Gtk.Button(label="Cancelar")
        cancel.connect("clicked", lambda _b: self.close())
        self.ok = Gtk.Button(label="Salvar")
        self.ok.add_css_class("suggested-action")
        self.ok.connect("clicked", lambda _b: self.save())
        header.pack_start(cancel)
        header.pack_end(self.ok)
        view = Adw.ToolbarView(content=body)
        view.add_top_bar(header)
        self.set_content(view)
        self.sync()

    def sync(self):
        self.ok.set_sensitive(bool(self.name_row.get_text().strip() and self.cmd_row.get_text().strip()))

    def pick_program(self):
        import shlex

        def chosen(d, res):
            try:
                self.cmd_row.set_text(f"{shlex.quote(d.open_finish(res).get_path())} {{arquivo}}")
            except GLib.Error:
                pass  # cancelado
        Gtk.FileDialog(title="Escolher programa").open(self, None, chosen)

    def save(self):
        self.on_save({"name": self.name_row.get_text().strip(), "icon": self.icon,
                      "command": self.cmd_row.get_text().strip(), "terminal": self.term_row.get_active()})
        self.close()


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="io.github.bergamota", flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.main = self.wheel = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        Adw.init()
        enable_pixbuf_formats()
        show = Gio.SimpleAction.new("show", GLib.VariantType.new("s"))
        show.connect("activate", lambda _a, p: self.get_wheel().open(p.get_string()))
        cancel = Gio.SimpleAction.new("cancel", None)
        cancel.connect("activate", lambda *_: self.wheel and self.wheel.cancel_later())
        hide = Gio.SimpleAction.new("hide", None)  # mesma tecla de novo: fecha na hora
        hide.connect("activate", lambda *_: self.wheel and self.wheel.close_wheel())
        settings = Gio.SimpleAction.new("settings", None)
        settings.connect("activate", lambda *_: Settings(self).present())
        for a in (show, cancel, hide, settings):
            self.add_action(a)
        self.hold()  # fica em segundo plano para a roda abrir rápido

    def get_wheel(self):
        self.wheel = self.wheel or Wheel(self)
        return self.wheel

    def win(self):
        if not self.main:
            self.main = Win(self)
            self.main.connect("close-request", lambda w: setattr(self, "main", None) or False)
        self.main.present()
        return self.main

    def do_activate(self):
        self.win()

    def do_open(self, files, _n, _hint):
        w = self.win()
        GLib.idle_add(w.show_menu, [Path(f.get_path()) for f in files if f.get_path()])


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        selftest()
    else:
        App().run(sys.argv)
