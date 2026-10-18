"""Runs on GitHub Actions. Downloads candidate photos for each job in fetch/jobs.json into out/<id>/.

Job fields:
  id      : folder name
  titles  : ["es:Ángel Labruna", "en:Ángel Labruna"]  -> lead image + images used on the page
  search  : ['"Ángel Labruna"']                        -> Commons file search
  must    : [["labruna"]]  any group whose tokens all appear in the (ascii-folded) file title
  urls    : ["https://..."]                            -> direct downloads (news sites, etc.)
  max     : max Wikimedia candidates (default 8)
  width   : thumb width (default 960)
"""
import json, os, re, sys, time, unicodedata, urllib.parse, urllib.request, io, traceback, subprocess
from PIL import Image

UA = "SubastaFutbolPhotoBot/1.0 (personal party game; github.com/AgustinReiden)"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
BAD = re.compile(r"(flag|bandera|escudo|logo|icon|crest|badge|trophy|copa_|cup_|medal|signature|firma|"
                 r"map|mapa|kit_|camiseta|template|stadium|estadio|commons-|wiki|\.svg$|\.gif$|\.tif|\.pdf$|\.webm|\.ogv|\.ogg)", re.I)


def fold(s):
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s.lower())


def get(url, ua=UA, tries=2):
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "*/*",
                                                       "Referer": "https://www.google.com/"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            err = e
            if e.code in (400, 403, 404):
                break
            time.sleep(2 * (k + 1))
        except Exception as e:
            err = e
            time.sleep(2 * (k + 1))
    raise err


def push(msg):
    try:
        subprocess.run("git add out && git commit -q -m '%s' && git push -q origin HEAD:tmp-fotos-subasta" % msg,
                       shell=True, timeout=120)
    except Exception as e:
        print("push fail", e, flush=True)


def api(host, **params):
    params.update(format="json", formatversion="2")
    url = f"https://{host}/w/api.php?" + urllib.parse.urlencode(params)
    time.sleep(0.15)
    return json.loads(get(url))


def match(title, must):
    if not must:
        return True
    t = fold(title)
    return any(all(tok in t for tok in grp) for grp in must)


def page_files(lang, title, must, log):
    host = f"{lang}.wikipedia.org"
    try:
        d = api(host, action="query", titles=title, redirects=1, prop="pageimages|images|pageprops",
                piprop="name", imlimit=200, ppprop="disambiguation")
    except Exception as e:
        log.append(f"api fail {lang}:{title} {e}")
        return []
    pages = d.get("query", {}).get("pages", [])
    out = []
    for pg in pages:
        if pg.get("missing"):
            log.append(f"missing {lang}:{title}")
            continue
        if "pageprops" in pg and "disambiguation" in pg["pageprops"]:
            log.append(f"disambiguation {lang}:{title}")
            continue
        lead = pg.get("pageimage")
        if lead:
            out.append((host, "File:" + lead, "lead"))
        for im in pg.get("images", []):
            t = im["title"]
            if BAD.search(t):
                continue
            if lead and t.endswith(lead.replace("_", " ")):
                continue
            if match(t, must):
                out.append((host, t, "page"))
    return out


def commons_search(q, must, log, limit=20):
    try:
        d = api("commons.wikimedia.org", action="query", list="search", srsearch=q, srnamespace=6, srlimit=limit)
    except Exception as e:
        log.append(f"search fail {q} {e}")
        return []
    out = []
    for r in d.get("query", {}).get("search", []):
        t = r["title"]
        if BAD.search(t):
            continue
        if match(t, must):
            out.append(("commons.wikimedia.org", t, "search"))
    return out


def file_info(host, title, width):
    d = api(host, action="query", titles=title, prop="imageinfo", iiprop="url|size|mime|extmetadata",
            iiurlwidth=width, iiextmetadatafilter="Artist|LicenseShortName|DateTimeOriginal|ImageDescription")
    pg = d["query"]["pages"][0]
    ii = pg.get("imageinfo", [{}])[0]
    return ii


def save_img(raw, path, maxside=1100):
    im = Image.open(io.BytesIO(raw))
    if getattr(im, "n_frames", 1) > 1:
        im.seek(0)
    im = im.convert("RGB")
    im.thumbnail((maxside, maxside), Image.LANCZOS)
    im.save(path, quality=88)
    return im.size


def run_job(job, root):
    jid = job["id"]
    d = os.path.join(root, jid)
    os.makedirs(d, exist_ok=True)
    log, meta = [], []
    must = job.get("must") or []
    width = job.get("width", 960)
    cands = []
    for t in job.get("titles", []):
        lang, title = t.split(":", 1)
        cands += page_files(lang, title, must, log)
    for q in job.get("search", []):
        cands += commons_search(q, must, log)
    seen, uniq = set(), []
    for host, t, kind in cands:
        key = fold(t.split(":", 1)[1])
        if key in seen:
            continue
        seen.add(key)
        uniq.append((host, t, kind))
    n = 0
    for host, t, kind in uniq:
        if n >= job.get("max", 8):
            break
        try:
            ii = file_info(host, t, width)
            if not ii or not ii.get("mime", "").startswith("image/") or ii.get("mime") in ("image/svg+xml", "image/gif"):
                continue
            if ii.get("width", 0) < 180 or ii.get("height", 0) < 180:
                continue
            url = ii.get("thumburl") or ii.get("url")
            raw = get(url)
            n += 1
            size = save_img(raw, os.path.join(d, f"w{n}.jpg"))
            em = ii.get("extmetadata", {})
            meta.append({"f": f"w{n}.jpg", "file": t, "kind": kind, "orig": [ii.get("width"), ii.get("height")],
                         "size": size, "page": ii.get("descriptionurl"),
                         "artist": re.sub("<[^>]+>", "", em.get("Artist", {}).get("value", ""))[:120],
                         "license": em.get("LicenseShortName", {}).get("value", ""),
                         "date": em.get("DateTimeOriginal", {}).get("value", "")[:40],
                         "desc": re.sub("<[^>]+>", "", em.get("ImageDescription", {}).get("value", ""))[:200]})
        except Exception as e:
            log.append(f"dl fail {t}: {e}")
    for k, u in enumerate(job.get("urls", []), 1):
        try:
            raw = get(u, ua=BROWSER_UA)
            size = save_img(raw, os.path.join(d, f"u{k}.jpg"))
            meta.append({"f": f"u{k}.jpg", "url": u, "size": size})
        except Exception as e:
            log.append(f"url fail {u}: {e}")
    with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump({"id": jid, "items": meta, "log": log, "candidates": len(uniq)}, fh, ensure_ascii=False, indent=1)
    return len(meta)


def main():
    jobs = json.load(open("fetch/jobs.json", encoding="utf-8"))
    root = jobs.get("out", "out")
    os.makedirs(root, exist_ok=True)
    # quick connectivity probe, recorded in the status file
    probe = []
    for u in ["https://commons.wikimedia.org/w/api.php?action=query&meta=siteinfo&format=json",
              "https://es.wikipedia.org/w/api.php?action=query&meta=siteinfo&format=json"]:
        try:
            probe.append(f"{u[:40]} OK {len(get(u))}")
        except Exception as e:
            probe.append(f"{u[:40]} FAIL {e}")
    with open(os.path.join(root, "_status.txt"), "a") as fh:
        fh.write("start %s\n%s\n" % (time.strftime("%H:%M:%S"), "\n".join(probe)))
    push("probe")
    summary = {}
    for f in jobs.get("files", []):
        try:
            raw = get(f["url"], ua=BROWSER_UA)
            p = os.path.join(root, f["path"])
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "wb").write(raw)
            summary[f["path"]] = len(raw)
        except Exception as e:
            summary[f["path"]] = f"ERROR {e}"
    for job in jobs["jobs"]:
        try:
            summary[job["id"]] = run_job(job, root)
        except Exception:
            summary[job["id"]] = "ERROR " + traceback.format_exc()[-300:]
        print(job["id"], summary[job["id"]], flush=True)
        with open(os.path.join(root, "_summary.json"), "w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=1)
        if len(summary) % 10 == 0:
            push(f"fotos parcial {len(summary)}")
    with open(os.path.join(root, "_status.txt"), "a") as fh:
        fh.write("done %s\n" % time.strftime("%H:%M:%S"))
    push("fotos final")


if __name__ == "__main__":
    main()
