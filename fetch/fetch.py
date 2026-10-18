"""Runs on GitHub Actions. Downloads candidate photos for each job in fetch/jobs.json into out/<id>/.

Job fields:
  id      : folder name
  titles  : ["es:Ángel Labruna", "en:Ángel Labruna"]  -> lead image + images used on the page
  search  : ['"Ángel Labruna"']                        -> Commons file search
  must    : [["labruna"]]  any group whose tokens all appear in the (ascii-folded) file title
  urls    : ["https://..."]                            -> direct downloads (news sites, etc.)
  max     : max Wikimedia candidates (default 6)
  redo    : true -> re-run even if results exist
Env: SHARD, NSHARDS for splitting jobs across parallel runners.
"""
import json, os, re, sys, time, unicodedata, urllib.parse, urllib.request, urllib.error, io, traceback, subprocess
from PIL import Image

UA = "SubastaFutbolPhotoBot/1.1 (personal party game; https://github.com/AgustinReiden; agustin.reiden.bot@users.noreply.github.com)"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
BAD = re.compile(r"(flag|bandera|escudo|logo|icon|crest|badge|trophy|copa_|cup_|medal|signature|firma|"
                 r"map|mapa|kit_|camiseta|template|stadium|estadio|commons-|wiki|\.svg$|\.gif$|\.tif|\.pdf$|\.webm|\.ogv|\.ogg)", re.I)
SHARD = int(os.environ.get("SHARD", "0"))
NSHARDS = int(os.environ.get("NSHARDS", "1"))
_last = {"t": 0.0}


def pace(gap):
    dt = time.time() - _last["t"]
    if dt < gap:
        time.sleep(gap - dt)
    _last["t"] = time.time()


def fold(s):
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s.lower())


def get(url, ua=UA, tries=6, gap=0.7):
    err = None
    for k in range(tries):
        pace(gap)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "*/*",
                                                       "Referer": "https://www.google.com/" if ua == BROWSER_UA else "https://commons.wikimedia.org/"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            err = e
            if e.code == 429 or e.code >= 500:
                ra = e.headers.get("Retry-After")
                wait = int(ra) if ra and ra.isdigit() else 8 * (k + 1)
                time.sleep(min(wait, 60))
                continue
            break
        except Exception as e:
            err = e
            time.sleep(3 * (k + 1))
    raise err


def api(host, **params):
    params.update(format="json", formatversion="2", maxlag="5")
    url = f"https://{host}/w/api.php?" + urllib.parse.urlencode(params)
    return json.loads(get(url, gap=1.0))


def match(title, must):
    if not must:
        return True
    t = fold(title)
    return any(all(tok in t.split() or tok in t for tok in grp) for grp in must)


def page_files(lang, title, must, log):
    host = f"{lang}.wikipedia.org"
    try:
        d = api(host, action="query", titles=title, redirects=1, prop="pageimages|images|pageprops",
                piprop="name", imlimit=200, ppprop="disambiguation")
    except Exception as e:
        log.append(f"api fail {lang}:{title} {e}")
        return []
    out = []
    for pg in d.get("query", {}).get("pages", []):
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


def infos(host, titles):
    """imageinfo for many titles in one call -> {normalized_title: ii}"""
    res = {}
    for i in range(0, len(titles), 20):
        chunk = titles[i:i + 20]
        d = api(host, action="query", titles="|".join(chunk), prop="imageinfo", iiprop="url|size|mime|extmetadata",
                iiurlwidth=960, iiextmetadatafilter="Artist|LicenseShortName|DateTimeOriginal|ImageDescription")
        norm = {n["from"]: n["to"] for n in d["query"].get("normalized", [])}
        bypage = {pg["title"]: pg for pg in d["query"]["pages"]}
        for t in chunk:
            pg = bypage.get(norm.get(t, t))
            if pg and pg.get("imageinfo"):
                res[t] = pg["imageinfo"][0]
    return res


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
    uniq = uniq[: job.get("max", 6) + 4]
    byhost = {}
    for host, t, kind in uniq:
        byhost.setdefault(host, []).append(t)
    ii_all = {}
    for host, ts in byhost.items():
        try:
            for t, ii in infos(host, ts).items():
                ii_all[(host, t)] = ii
        except Exception as e:
            log.append(f"info fail {host}: {e}")
    n = 0
    for host, t, kind in uniq:
        if n >= job.get("max", 6):
            break
        ii = ii_all.get((host, t))
        if not ii:
            continue
        if not ii.get("mime", "").startswith("image/") or ii.get("mime") in ("image/svg+xml", "image/gif"):
            continue
        if ii.get("width", 0) < 180 or ii.get("height", 0) < 180:
            continue
        url = ii.get("thumburl") or ii.get("url")
        try:
            raw = get(url, gap=0.8)
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
            raw = get(u, ua=BROWSER_UA, tries=3, gap=0.3)
            size = save_img(raw, os.path.join(d, f"u{k}.jpg"))
            meta.append({"f": f"u{k}.jpg", "url": u, "size": size})
        except Exception as e:
            log.append(f"url fail {u}: {e}")
    with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump({"id": jid, "items": meta, "log": log, "candidates": len(uniq)}, fh, ensure_ascii=False, indent=1)
    return len(meta)


def push(msg):
    cmd = ("git add out && (git commit -q -m '%s' || true) && "
           "for i in 1 2 3 4 5; do git pull -q --rebase origin tmp-fotos-subasta && git push -q origin HEAD:tmp-fotos-subasta && break; sleep $((i*3)); done") % msg
    try:
        subprocess.run(cmd, shell=True, timeout=180)
    except Exception as e:
        print("push fail", e, flush=True)


def done_ok(root, jid):
    p = os.path.join(root, jid, "meta.json")
    if not os.path.exists(p):
        return False
    m = json.load(open(p, encoding="utf-8"))
    bad = any("429" in l for l in m.get("log", []))
    return len(m.get("items", [])) >= 3 and not bad


def main():
    jobs = json.load(open("fetch/jobs.json", encoding="utf-8"))
    root = jobs.get("out", "out")
    os.makedirs(root, exist_ok=True)
    summary = {}
    if SHARD == 0:
        for f in jobs.get("files", []):
            p = os.path.join(root, f["path"])
            if os.path.exists(p):
                continue
            try:
                raw = get(f["url"], ua=BROWSER_UA, tries=2, gap=0.1)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                open(p, "wb").write(raw)
            except Exception as e:
                print("file fail", e)
    mine = [j for i, j in enumerate(jobs["jobs"]) if i % NSHARDS == SHARD]
    count = 0
    for job in mine:
        if not job.get("redo") and done_ok(root, job["id"]):
            continue
        try:
            summary[job["id"]] = run_job(job, root)
        except Exception:
            summary[job["id"]] = "ERROR " + traceback.format_exc()[-300:]
        print(job["id"], summary[job["id"]], flush=True)
        count += 1
        if count % 6 == 0:
            push(f"fotos shard {SHARD} parcial {count}")
    with open(os.path.join(root, f"_status_{SHARD}.txt"), "w") as fh:
        fh.write("done %s %s\n" % (time.strftime("%H:%M:%S"), json.dumps(summary, ensure_ascii=False)))
    push(f"fotos shard {SHARD} final")


if __name__ == "__main__":
    main()
