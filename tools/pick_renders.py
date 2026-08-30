"""Picker local pour choisir un rendu Midjourney par joueur. (OFFLINE, dev only.)

Ouvre une petite interface web sur http://localhost:8765 : pour chaque joueur, le
portrait officiel LNH a gauche, les rendus Midjourney a droite. Tu choisis au clavier,
le manifest est ecrit au fil de l'eau (aucun bouton "enregistrer" a oublier).

Le rapprochement fichier -> joueur repose sur l'ORDRE : les prompts d'un club ont ete
colles dans l'ordre de out/paste_order.json, donc les rendus reviennent dans ce meme
ordre. Comme cette hypothese peut deraper (un job relance, un rendu supprime), la
photo de reference est affichee en vis-a-vis : si les visages ne correspondent pas,
tu recales tout le club d'un cran avec [ ou ].

Entree : data/roster_s2.json, out/paste_order.json
         <downloads>/<club>/*.png   (un sous-dossier par club) ou <downloads>/*.png avec --club
Sortie : data/roster_s2.json  (champ image_file)
         out/pick_offsets.json (recalages manuels, rejoues au prochain lancement)

Usage :
    python tools/pick_renders.py --downloads "C:/Users/quent/Downloads/mj"
    python tools/pick_renders.py --downloads "C:/.../mj/aix" --club Aix
    python tools/pick_renders.py --downloads ... --per-player 1   # si tu telecharges la grille 2x2

Raccourcis : 1-9 choisir | fleches naviguer | 0 effacer | [ ] recaler le club | Entree prochain non traite
"""
import argparse
import json
import mimetypes
import os
import re
import unicodedata
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "data", "roster_s2.json")
ORDER = os.path.join(ROOT, "out", "paste_order.json")
OFFSETS = os.path.join(ROOT, "out", "pick_offsets.json")

IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")
CFG = {}  # rempli par main() : downloads, per_player, club


def slugify(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()


def load_json(path, default):
    return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def club_files(club):
    """Rendus d'un club, tries par date puis par nom = ordre de generation."""
    base = CFG["downloads"]
    sub = os.path.join(base, slugify(club))
    folder = sub if os.path.isdir(sub) else base
    if folder == base and CFG["club"] and slugify(CFG["club"]) != slugify(club):
        return []
    try:
        names = [f for f in os.listdir(folder) if f.lower().endswith(IMG_EXT)]
    except OSError:
        return []
    names.sort(key=lambda f: (os.path.getmtime(os.path.join(folder, f)), f))
    return [os.path.join(folder, f) for f in names]


def build_state():
    players = {p["id"]: p for p in load_json(MANIFEST, [])}
    order = load_json(ORDER, {})
    offsets = load_json(OFFSETS, {})
    n = CFG["per_player"]
    pool = CFG["pool"]

    out = []
    for club in sorted(order):
        files = club_files(club)
        if not files and not CFG["show_empty"]:
            continue
        off = int(offsets.get(club, 0))
        # En mode pool (une seule image par joueur, deja triee dans Midjourney), on
        # propose TOUTE la banque du club a chaque joueur : l'ordre de telechargement
        # n'a alors plus besoin de correspondre a l'ordre de collage.
        pris = {}
        if pool:
            for pid in order[club]:
                choix = (players.get(pid) or {}).get("image_file")
                if choix:
                    pris[choix] = (players[pid]["nom"], pid)

        for i, pid in enumerate(order[club]):
            p = players.get(pid)
            if not p:
                continue
            if pool:
                cands = files
            else:
                start = (i + off) * n
                cands = files[start:start + n] if start >= 0 else []
            out.append({
                "id": pid,
                "nom": p["nom"],
                "club": club,
                "poste": p.get("poste") or "",
                "rarete": p.get("rarete") or "",
                "ref": p.get("ref_file") or "",
                "candidats": [rel(c) for c in cands],
                "pris": {rel(f): v[0] for f, v in pris.items()} if pool else {},
                "choisi": p.get("image_file") or "",
                "offset": off,
                "pool": pool,
            })
    return out


def rel(chemin):
    return (os.path.relpath(chemin, ROOT).replace("\\", "/")
            if chemin.startswith(ROOT) else chemin)


def prefill(club):
    """Pre-affecte les images du club aux joueurs sans choix, dans l'ordre. Sert de
    point de depart quand l'ordre de telechargement suit l'ordre de collage ; il ne
    reste alors qu'a corriger les quelques decalages."""
    players = load_json(MANIFEST, [])
    par_id = {p["id"]: p for p in players}
    ids = load_json(ORDER, {}).get(club, [])
    libres = [f for f in club_files(club)
              if rel(f) not in {par_id[i].get("image_file") for i in ids if i in par_id}]
    n = 0
    for pid in ids:
        p = par_id.get(pid)
        if not p or p.get("image_file") or not libres:
            continue
        p["image_file"] = rel(libres.pop(0))
        n += 1
    save_json(MANIFEST, players)
    return n


def set_choice(pid, path):
    players = load_json(MANIFEST, [])
    for p in players:
        if p["id"] == pid:
            p["image_file"] = path or None
            break
    save_json(MANIFEST, players)


def set_offset(club, off):
    offsets = load_json(OFFSETS, {})
    offsets[club] = int(off)
    save_json(OFFSETS, offsets)


PAGE = r"""<!doctype html><meta charset="utf-8"><title>Picker rendus S2</title>
<style>
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:#14161a;color:#e8eaed;font:14px/1.5 system-ui,sans-serif}
header{position:sticky;top:0;z-index:5;background:#1b1e24;border-bottom:1px solid #2c313a;
 padding:10px 16px;display:flex;gap:18px;align-items:center;flex-wrap:wrap}
h1{font-size:15px;margin:0;font-weight:600}
.stat{color:#9aa3af;font-size:13px}
.bar{flex:1;height:6px;background:#2c313a;border-radius:3px;overflow:hidden;min-width:120px}
.bar i{display:block;height:100%;background:#4ea1ff}
kbd{background:#2c313a;border:1px solid #3a4150;border-radius:4px;padding:1px 5px;font-size:11px}
.row{display:flex;gap:14px;padding:14px 16px;border-bottom:1px solid #22262d;align-items:flex-start}
.row.cur{background:#1b2230;box-shadow:inset 3px 0 0 #4ea1ff}
.row.done .meta b{color:#6ee7a8}
.meta{width:210px;flex:none}
.meta b{display:block;font-size:15px}
.meta span{color:#9aa3af;font-size:12px}
.ref img{width:118px;border-radius:6px;background:#fff}
.cands{display:flex;gap:10px;flex-wrap:wrap;flex:1}
.cand{position:relative;cursor:pointer;border:2px solid transparent;border-radius:8px;
 overflow:hidden;background:#0e1013;line-height:0}
.cand img{width:132px;display:block}
.pool .cand img{width:92px}
.cand.sel{border-color:#4ea1ff}
.cand.pris{opacity:.28}
.cand.pris.sel{opacity:1}
.cand em{position:absolute;top:3px;left:3px;background:#000a;border-radius:4px;
 padding:0 5px;font:11px/18px monospace;font-style:normal}
.cand b{position:absolute;bottom:0;left:0;right:0;background:#000c;font:10px/14px sans-serif;
 font-weight:400;text-align:center;overflow:hidden;white-space:nowrap}
button{background:#2c313a;color:#e8eaed;border:1px solid #3a4150;border-radius:5px;
 padding:4px 10px;font:13px system-ui;cursor:pointer}
button:hover{background:#3a4150}
.empty{color:#6b7280;font-style:italic;padding:8px 0}
.rar{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:5px}
</style>
<header>
 <h1>Picker rendus S2</h1>
 <div class=bar><i id=bar></i></div>
 <div class=stat id=stat></div>
 <button onclick="prefill()" title="Affecte les images restantes du club dans l'ordre">
  Pre-affecter ce club <kbd>P</kbd></button>
 <div class=stat><kbd>1-9</kbd> choisir <kbd>&larr;&rarr;</kbd> naviguer
  <kbd>0</kbd> effacer <kbd>[ ]</kbd> recaler le club <kbd>&crarr;</kbd> prochain non traite</div>
</header>
<div id=list></div>
<script>
const RAR={"Commun":"#969aa2","Peu Commun":"#46b969","Rare":"#3782eb","Épique":"#aa5aeb","Légendaire":"#f0bc37"};
let S=[],cur=0;
const src=p=>"/img?p="+encodeURIComponent(p);

async function load(){S=await (await fetch("/api/state")).json();draw();}

function draw(){
 const done=S.filter(p=>p.choisi).length;
 bar.style.width=(S.length?done/S.length*100:0)+"%";
 // 16 images pour 16 joueurs : toute image affectee deux fois est une erreur,
 // et elle laisse forcement un autre joueur sans rendu.
 const vus={},dbl=new Set();
 S.filter(p=>p.choisi).forEach(p=>{if(vus[p.choisi])dbl.add(p.choisi);vus[p.choisi]=1;});
 stat.innerHTML=done+" / "+S.length+" choisis"
  +(dbl.size?` &nbsp;<span style="color:#ff8080">${dbl.size} image${dbl.size>1?"s":""} affectee${dbl.size>1?"s":""} a plusieurs joueurs</span>`:"");
 list.innerHTML=S.map((p,i)=>`
  <div class="row${i==cur?" cur":""}${p.choisi?" done":""}" id="r${i}">
   <div class=meta>
    <b>${p.nom}</b>
    <span><i class=rar style="background:${RAR[p.rarete]||"#555"}"></i>${p.club} · ${p.poste}</span>
    <span style="color:#6b7280">${p.rarete}${p.offset?" · recal "+p.offset:""}</span>
   </div>
   <div class=ref>${p.ref?`<img src="${src(p.ref)}">`:""}</div>
   <div class="cands${p.pool?" pool":""}">${p.candidats.length?p.candidats.map((c,j)=>{
     const autre=p.pris&&p.pris[c]&&p.pris[c]!=p.nom;
     return `<div class="cand${p.choisi==c?" sel":""}${autre?" pris":""}" onclick="pick(${i},${j})">
       <em>${j+1}</em><img loading=lazy src="${src(c)}">${autre?`<b>${p.pris[c]}</b>`:""}</div>`;
    }).join("")
     :'<div class=empty>aucun rendu telecharge pour ce joueur</div>'}</div>
  </div>`).join("");
 document.getElementById("r"+cur)?.scrollIntoView({block:"center",behavior:"smooth"});
}

async function pick(i,j){
 const p=S[i],f=j===null?"":p.candidats[j];
 p.choisi=f;
 await fetch("/api/select",{method:"POST",body:JSON.stringify({id:p.id,file:f})});
 if(j!==null&&i===cur&&cur<S.length-1)cur++;
 draw();
}

async function prefill(){
 const club=S[cur].club;
 const r=await (await fetch("/api/prefill",{method:"POST",
   body:JSON.stringify({club:club})})).json();
 await load();
 stat.textContent=`${r.n} images affectees a ${club}`;
}

async function shift(d){
 const club=S[cur].club;
 await fetch("/api/offset",{method:"POST",
   body:JSON.stringify({club:club,offset:(S[cur].offset||0)+d})});
 await load();
}

addEventListener("keydown",e=>{
 if(e.key>="1"&&e.key<="9"){const j=+e.key-1;if(S[cur].candidats[j])pick(cur,j);}
 else if(e.key==="0")pick(cur,null);
 else if(e.key==="ArrowRight"||e.key==="ArrowDown"){cur=Math.min(cur+1,S.length-1);draw();}
 else if(e.key==="ArrowLeft"||e.key==="ArrowUp"){cur=Math.max(cur-1,0);draw();}
 else if(e.key==="[")shift(-1);
 else if(e.key==="]")shift(1);
 else if(e.key==="p"||e.key==="P")prefill();
 else if(e.key==="Enter"){const n=S.findIndex((p,i)=>i>cur&&!p.choisi);
   if(n>=0){cur=n;draw();}}
 else return;
 e.preventDefault();
});
load();
</script>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # silence : le terminal sert au rapport final

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        body = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == "/":
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if url.path == "/api/state":
            return self._send(200, json.dumps(build_state(), ensure_ascii=False))
        if url.path == "/img":
            raw = urllib.parse.parse_qs(url.query).get("p", [""])[0]
            path = raw if os.path.isabs(raw) else os.path.join(ROOT, raw)
            path = os.path.realpath(path)
            # on ne sert que le repo et le dossier de telechargements
            roots = (os.path.realpath(ROOT), os.path.realpath(CFG["downloads"]))
            if not path.startswith(roots) or not os.path.isfile(path):
                return self._send(404, b"", "text/plain")
            ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
            with open(path, "rb") as fh:
                return self._send(200, fh.read(), ctype)
        return self._send(404, b"", "text/plain")

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        data = json.loads(self.rfile.read(n) or "{}")
        if self.path == "/api/select":
            set_choice(data["id"], data.get("file", ""))
        elif self.path == "/api/offset":
            set_offset(data["club"], data["offset"])
        elif self.path == "/api/prefill":
            return self._send(200, json.dumps({"ok": True, "n": prefill(data["club"])}))
        else:
            return self._send(404, b"", "text/plain")
        return self._send(200, json.dumps({"ok": True}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--downloads", required=True, help="dossier des rendus Midjourney")
    ap.add_argument("--club", default="", help="si le dossier ne contient qu'un club")
    ap.add_argument("--per-player", type=int, default=4,
                    help="images par joueur : 4 (les 4 rendus d'un job) ou 1 (deja trie dans MJ)")
    ap.add_argument("--pool", action="store_true",
                    help="propose toute la banque du club a chaque joueur (auto si --per-player 1)")
    ap.add_argument("--show-empty", action="store_true",
                    help="affiche aussi les clubs sans rendu telecharge")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()

    if not os.path.isdir(args.downloads):
        raise SystemExit(f"Dossier introuvable : {args.downloads}")
    if not os.path.exists(ORDER):
        raise SystemExit("out/paste_order.json absent : lance d'abord tools/build_prompts_s2.py")

    CFG.update(downloads=os.path.abspath(args.downloads), club=args.club,
               per_player=args.per_player, show_empty=args.show_empty,
               pool=args.pool or args.per_player == 1)

    state = build_state()
    avec = sum(1 for p in state if p["candidats"])
    print(f"{len(state)} joueurs charges, {avec} avec des rendus a departager.")
    if not avec:
        print("Aucun rendu trouve : verifie --downloads (un sous-dossier par club,")
        print("ou --club <nom> si tout est en vrac dans un seul dossier).")

    url = f"http://localhost:{args.port}/"
    print(f"\nPicker sur {url}   (Ctrl+C pour arreter)")
    webbrowser.open(url)
    try:
        HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
    except KeyboardInterrupt:
        players = load_json(MANIFEST, [])
        choix = [p["image_file"] for p in players if p.get("image_file")]
        print(f"\nArret. {len(choix)} rendus affectes dans data/roster_s2.json")
        doublons = len(choix) - len(set(choix))
        if doublons:
            print(f"ATTENTION {doublons} image(s) affectee(s) a plusieurs joueurs.")


if __name__ == "__main__":
    main()
