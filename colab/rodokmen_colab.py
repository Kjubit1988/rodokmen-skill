#!/usr/bin/env python3
"""Rodokmen v Google Colabu: instalace, přihlášení, záloha na Google Disk, náhled webu a chat s AI.

Volá ho notebook colab/Rodokmen.ipynb. Práci dělá jeden ze dvou agentů, oba se stejným skillem rodokmen:
  - Claude Code (Anthropic) – klíč ANTHROPIC_API_KEY (nebo CLAUDE_CODE_OAUTH_TOKEN) v Tajných klíčích Colabu,
  - Gemini CLI (Google) – klíč GEMINI_API_KEY.
Projekt leží na rychlém disku virtuálního počítače (/content/Rodokmen). Po každé odpovědi se zkopíruje
na Google Disk (Můj disk/Rodokmen) a při dalším spuštění se odtud obnoví.

Pro zkoušení mimo Colab: RODOKMEN_DISK=<složka> nahradí Google Disk obyčejnou složkou.
"""
import glob, json, os, re, shutil, subprocess, sys, time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(REPO, "skills", "rodokmen")
PROJEKT = os.environ.get("RODOKMEN_PROJEKT", "/content/Rodokmen")
UKAZKA = PROJEKT + "-ukazka"
DISK_KOREN = "/content/drive"
DISK = os.environ.get("RODOKMEN_DISK", DISK_KOREN + "/MyDrive/Rodokmen")
STAV = os.environ.get("RODOKMEN_STAV", "/content/.rodokmen")   # relace a nastavení jen pro tento virtuální počítač
VYNECHAT = {"node_modules", "dist", ".astro", "__pycache__"}  # na Disk se nekopíruje (dá se kdykoli znovu vyrobit)
PORT = 4321

# volba v notebooku → (agent, model, ceny $ za 1M tokenů: vstup, výstup, čtení z cache); ceník k 20. 10. 2026
MODELY = {
    "Claude Sonnet 5.5": ("claude", "claude-sonnet-5-5", (2.00, 10.00, 0.20)),
    "Claude Opus 5.5": ("claude", "claude-opus-5-5", (4.00, 20.00, 0.20)),
    "Gemini 3.1 Pro": ("gemini", "gemini-3.1-pro-preview", (2.00, 12.00, 0.20)),
    "Gemini 3.8 Flash": ("gemini", "gemini-3.8-flash", (0.75, 3.75, 0.075)),
}
ZKRATKY = {"sonnet": "Claude Sonnet 5.5", "opus": "Claude Opus 5.5", "pro": "Gemini 3.1 Pro", "flash": "Gemini 3.8 Flash"}
KLICE = {"claude": ["ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"], "gemini": ["GEMINI_API_KEY"]}

# Agent pracuje bez ptaní (v notebooku se nedá nic schvalovat), na Google Disk ale sahat nesmí – zálohu dělá notebook.
# Je to pojistka, ne neprůstřelná zeď: příkaz v shellu se dá napsat i tak, aby pravidlu unikl.
NASTAVENI_CLAUDE = {"permissions": {
    "allow": ["Bash", "Read", "Edit", "Write", "Glob", "Grep", "WebFetch", "WebSearch", "Agent", "Skill", "TodoWrite"],
    "deny": [f"Read(/{DISK_KOREN}/**)", f"Edit(/{DISK_KOREN}/**)", f"Write(/{DISK_KOREN}/**)", f"Bash(*{DISK_KOREN}*)"],
}}
ZAKAZY_GEMINI = f"""# Rodokmen v Colabu: Google Disk zálohuje notebook, agent na něj nesahá.
[[rule]]
toolName = "*"
argsPattern = "{DISK_KOREN}"
decision = "deny"
priority = 990
denyMessage = "Google Disk je jen pro zálohu, kterou dělá notebook. Pracuj v {PROJEKT}."
"""
SAMO_KONEC = "HOTOVO-SAMO"
SAMO_POKYN = ("[Samostatný výzkum – kolo {kolo} z nejvýš {kol}] Pracuj sám podle sekce „Samostatný výzkum“ "
              "ve skillu rodokmen: vezmi další cíl, dotáhni ho, zapiš a ulož. Na nic se neptej. Na konci napiš "
              f"2–4 věty, co jsi zjistil. Když bez uživatele nemůžeš pokračovat, napiš na poslední řádek {SAMO_KONEC} a proč.")
POZNAMKA_COLAB = f"""# Rodokmen v Google Colabu
Běžíš v Google Colabu, ne na počítači uživatele. Uživatel s tebou mluví přes notebook Rodokmen.ipynb
a tvoje odpovědi vidí jako Markdown. Projekt je v {PROJEKT}. Řiď se skillem rodokmen a jeho sekcí „Google Colab“.
"""

os.environ["RODOKMEN_COLAB"] = "1"
os.environ.setdefault("DISABLE_AUTOUPDATER", "1")


# ---------- instalace ----------

def pripravit(volba):
    """Nainstaluje programy, které skill potřebuje, a agenta pro zvolený model. Poprvé 1–3 minuty."""
    agent = MODELY[volba][0]
    print("Připravuji programy…")
    if not shutil.which("convert"):
        _sh("apt-get -qq update && DEBIAN_FRONTEND=noninteractive apt-get -qq install -y imagemagick")
    _povolit_velke_obrazky()
    _nahrada_magick()
    _node()
    prikaz, balicek = ("claude", "@anthropic-ai/claude-code") if agent == "claude" else ("gemini", "@google/gemini-cli")
    if not shutil.which(prikaz):
        print(f"Instaluji {prikaz}…")
        _sh(f"npm install -g --silent --no-audit --no-fund {balicek}")
    _nainstalovat_skill()
    if not _sh("git config --global user.name || true").strip():
        _sh(["git", "config", "--global", "user.name", "Rodokmen (Colab)"])
        _sh(["git", "config", "--global", "user.email", "rodokmen@colab.invalid"])
    print(f"✓ Hotovo: {prikaz} {_sh([prikaz, '--version']).strip()}, ImageMagick, Node.js {_sh(['node', '--version']).strip()}")


def _povolit_velke_obrazky():
    """ImageMagick 6 v Ubuntu má nízké limity; slepené snímky matrik bývají velké."""
    cesta = "/etc/ImageMagick-6/policy.xml"
    if not os.path.exists(cesta):
        return
    txt = open(cesta, encoding="utf-8").read()
    for nazev, hodnota in {"memory": "2GiB", "map": "4GiB", "width": "64KP", "height": "64KP",
                           "area": "1GP", "disk": "8GiB"}.items():
        txt = re.sub(rf'(name="{nazev}" value=")[^"]*"', rf'\g<1>{hodnota}"', txt)
    open(cesta, "w", encoding="utf-8").write(txt)


def _nahrada_magick():
    """Nástroje skillu volají `magick` (ImageMagick 7); Ubuntu má ImageMagick 6 s příkazy convert, identify…"""
    if shutil.which("magick"):
        return
    cesta = "/usr/local/bin/magick"
    with open(cesta, "w") as f:
        f.write('#!/bin/bash\n# magick (ImageMagick 7) -> příkazy ImageMagick 6\n'
                'case "$1" in\n'
                '  identify|montage|mogrify|composite|compare|conjure|stream) n="$1"; shift; exec "$n" "$@" ;;\n'
                '  *) exec convert "$@" ;;\n'
                'esac\n')
    os.chmod(cesta, 0o755)


def _node():
    verze = _sh("node --version 2>/dev/null || true").strip()
    if not verze or int(verze.lstrip("v").split(".")[0]) < 20:
        print("Instaluji Node.js 22…")
        _sh("curl -fsSL https://deb.nodesource.com/setup_22.x | bash - >/dev/null && apt-get -qq install -y nodejs")


def _nainstalovat_skill():
    """Stejný skill pro oba agenty, plus poznámka, že běží v Colabu."""
    for slozka, soubor in (("~/.claude", "CLAUDE.md"), ("~/.gemini", "GEMINI.md")):
        domov = os.path.expanduser(slozka)
        cil = os.path.join(domov, "skills", "rodokmen")
        shutil.rmtree(cil, ignore_errors=True)
        shutil.copytree(SKILL, cil)
        open(os.path.join(domov, soubor), "w", encoding="utf-8").write(POZNAMKA_COLAB)
    # Gemini CLI má číst i CLAUDE.md z projektu (návod, jak navázat)
    nastaveni = os.path.expanduser("~/.gemini/settings.json")
    data = json.load(open(nastaveni)) if os.path.exists(nastaveni) else {}
    data.setdefault("context", {})["fileName"] = ["GEMINI.md", "CLAUDE.md"]
    json.dump(data, open(nastaveni, "w"), indent=2)
    os.makedirs(STAV, exist_ok=True)
    open(os.path.join(STAV, "zakazy.toml"), "w", encoding="utf-8").write(ZAKAZY_GEMINI)


# ---------- přihlášení ----------

def prihlasit(volba):
    """Načte klíč z Tajných klíčů Colabu (🔑 v levém panelu), jinak se na něj zeptá."""
    jmena = KLICE[MODELY[volba][0]]
    for jmeno in jmena + ["NETLIFY_AUTH_TOKEN"]:
        hodnota = _tajny_klic(jmeno)
        if hodnota:
            os.environ[jmeno] = hodnota
    if not any(os.environ.get(j) for j in jmena):
        from getpass import getpass
        hodnota = getpass(f"Vložte {jmena[0]} (příště ho uložte do Tajných klíčů Colabu, ikona 🔑 vlevo): ").strip()
        if not hodnota:
            raise SystemExit("Bez klíče to nepůjde.")
        os.environ[jmena[0]] = hodnota
    print(f"✓ Klíč pro {volba} je připravený.")


def _tajny_klic(jmeno):
    try:
        from google.colab import userdata
        return userdata.get(jmeno)
    except Exception:   # mimo Colab, klíč neexistuje nebo k němu notebook nemá přístup
        return None


# ---------- projekt a záloha ----------

def otevrit_projekt():
    """Obnoví projekt z Google Disku, nebo založí nový."""
    _pripojit_disk()
    if os.path.exists(os.path.join(PROJEKT, "data", "STAV.md")):
        print("Projekt už je otevřený:", PROJEKT)
    elif os.path.exists(os.path.join(DISK, "data", "STAV.md")):
        print("Obnovuji projekt z Google Disku…")
        _kopirovat(DISK, PROJEKT)
    else:
        print("Zakládám nový projekt…")
        _sh([sys.executable, "-I", os.path.join(SKILL, "tools", "novy_projekt.py"), PROJEKT])
        _sh("git init -q && git add -A && git commit -qm 'Založení projektu'", cwd=PROJEKT)
    # nástroje vždy z aktuální verze skillu (data se nepřepisují)
    _sh([sys.executable, "-I", os.path.join(SKILL, "tools", "novy_projekt.py"), PROJEKT])
    zalohovat()


def zalohovat(tise=False):
    """Zkopíruje projekt na Google Disk (jen změněné soubory, nic tam nemaže)."""
    if not _disk_pripojen():
        print("Google Disk není připojený – nezálohuji. Spusťte znovu buňku s otevřením projektu.")
        return
    n = _kopirovat(PROJEKT, DISK)
    if not tise:
        print(f"✓ Uloženo na Google Disk (Můj disk/Rodokmen), změněných souborů: {n}")


def _pripojit_disk():
    if _disk_pripojen():
        return
    from google.colab import drive
    drive.mount(DISK_KOREN)


def _disk_pripojen():
    return bool(os.environ.get("RODOKMEN_DISK")) or os.path.isdir(os.path.join(DISK_KOREN, "MyDrive"))


def _kopirovat(zdroj, cil):
    """Jednosměrná synchronizace: zkopíruje nové a změněné soubory, v cíli nic nemaže."""
    n = 0
    for base, dirs, files in os.walk(zdroj):
        dirs[:] = [d for d in dirs if d not in VYNECHAT]
        slozka = os.path.join(cil, os.path.relpath(base, zdroj))
        os.makedirs(slozka, exist_ok=True)
        for f in files:
            a, b = os.path.join(base, f), os.path.join(slozka, f)
            if os.path.islink(a):
                continue
            sa = os.stat(a)
            sb = os.stat(b) if os.path.exists(b) else None
            if sb and sb.st_size == sa.st_size and sb.st_mtime >= sa.st_mtime - 2:
                continue
            shutil.copy2(a, b)
            n += 1
    return n


# ---------- web ----------

_server = None


def nahled(slozka=PROJEKT):
    """Postaví web a ukáže odkaz na náhled (místo `npm run nahled` a localhost)."""
    global _server
    web = os.path.join(slozka, "web")
    if not os.path.isdir(os.path.join(web, "node_modules")):
        print("Instaluji knihovny webu (jednou, asi minutu)…")
        _sh("npm install --silent --no-audit --no-fund", cwd=web)
    print("Stavím web…")
    _sh("npm run --silent data && npm run --silent build", cwd=web)
    if _server and _server.poll() is None:
        _server.terminate()
        _server.wait()
    _server = subprocess.Popen([sys.executable, "-m", "http.server", str(PORT), "--bind", "127.0.0.1",
                                "--directory", os.path.join(web, "dist")],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1)
    try:
        from google.colab import output
        output.serve_kernel_port_as_window(PORT, path="/")
    except ImportError:
        print(f"Náhled: http://localhost:{PORT}/")


def ukazka():
    """Ukázkový web s vymyšlenou rodinou Novákových."""
    if not os.path.exists(os.path.join(UKAZKA, "data", "STAV.md")):
        _sh([sys.executable, "-I", os.path.join(SKILL, "tools", "novy_projekt.py"), UKAZKA, "--ukazka"])
    nahled(UKAZKA)


def nahrat():
    """Nahraje soubory z počítače nebo mobilu do dokumenty/."""
    from google.colab import files
    cil = os.path.join(PROJEKT, "dokumenty")
    os.makedirs(cil, exist_ok=True)
    nahrane = files.upload()
    for jmeno, data in nahrane.items():
        jmeno = os.path.basename(jmeno)
        open(os.path.join(cil, jmeno), "wb").write(data)
        kopie = os.path.join(os.getcwd(), jmeno)   # files.upload() ukládá i do aktuální složky
        if os.path.abspath(kopie) != os.path.join(cil, jmeno) and os.path.isfile(kopie):
            os.remove(kopie)
    if nahrane:
        print("✓ Nahráno do dokumenty/:", ", ".join(map(os.path.basename, nahrane)))
        print("Napište, co v nich je, třeba: „Nahrál jsem oddací list babičky.“")


def ukaz(soubor):
    """Zobrazí obrázek nebo stránku z projektu."""
    cesta = soubor if os.path.isabs(soubor) else os.path.join(PROJEKT, soubor)
    if not os.path.exists(cesta):
        print("Soubor nenalezen:", soubor)
        return
    from IPython.display import HTML, Image, display
    if cesta.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
        display(Image(filename=cesta, width=900))
    elif cesta.lower().endswith((".html", ".svg")):
        display(HTML(open(cesta, encoding="utf-8").read()))
    else:
        print(open(cesta, encoding="utf-8", errors="replace").read()[:5000])


# ---------- chat ----------

NAPOVEDA = """Příkazy (místo zprávy):
  :nahraj          nahrát dokumenty a fotky (rodné listy, parte, fotky…) do dokumenty/
  :nahled          postavit web a ukázat náhled
  :ukazka          ukázkový web s vymyšlenou rodinou
  :ukaz <soubor>   zobrazit obrázek z projektu
  :samo [kola] [$limit] [minuty m]
                   AI hledá v matrikách sama, kolo po kole (výchozí 10 kol, $5, 120 m); zastavíte ■
  :model <název>   přepnout model: sonnet, opus, pro, flash
  :uloz            hned uložit na Google Disk (jinak se ukládá po každé odpovědi)
  :konec           uložit a skončit"""


def chat(volba):
    """Rozhovor s agentem. Každá zpráva = jedno spuštění agenta; relace pokračuje, dokud běží tento počítač."""
    stav = _nacist_stav()
    stav["volba"] = volba
    print(f"Model: {volba}. Napište zprávu a stiskněte Enter. Začněte třeba: Chci udělat rodokmen své rodiny.")
    print(NAPOVEDA)
    while True:
        try:
            zprava = input("\nVy: ").strip()
        except (KeyboardInterrupt, EOFError):
            break
        if not zprava:
            continue
        if zprava.startswith(":"):
            if _prikaz(zprava, stav) == "konec":
                break
            continue
        try:
            _poslat(zprava, stav)
        except KeyboardInterrupt:
            print("\n(Přerušeno. Můžete psát dál.)")
        zalohovat(tise=True)
    zalohovat()
    print("Konec. Příště spusťte notebook znovu a pokračujte – AI naváže podle data/STAV.md.")


def _prikaz(text, stav):
    prikaz, _, arg = text[1:].partition(" ")
    prikaz, arg = prikaz.lower(), arg.strip()
    try:
        if prikaz in ("konec", "k"):
            return "konec"
        elif prikaz == "nahled":
            nahled()
        elif prikaz == "ukazka":
            ukazka()
        elif prikaz == "nahraj":
            nahrat()
        elif prikaz == "ukaz" and arg:
            ukaz(arg)
        elif prikaz == "uloz":
            zalohovat()
        elif prikaz == "samo":
            limity = {}
            for slovo in arg.split():
                if slovo.startswith("$"):
                    limity["limit_usd"] = float(slovo[1:].replace(",", "."))
                elif slovo.lower().endswith("m"):
                    limity["minut"] = int(slovo[:-1])
                else:
                    limity["kol"] = int(slovo)
            _samo(stav, **limity)
        elif prikaz == "model" and ZKRATKY.get(arg.lower()):
            nova = ZKRATKY[arg.lower()]
            if MODELY[nova][0] != MODELY[stav["volba"]][0]:
                pripravit(nova)
                prihlasit(nova)
                print("Jiný agent začne nový rozhovor; stav projektu si přečte z data/STAV.md.")
            stav["volba"] = nova
            _ulozit_stav(stav)
            print("✓ Model:", nova)
        else:
            print(NAPOVEDA)
    except Exception as e:   # chyba příkazu nesmí ukončit rozhovor
        print("Chyba:", e)


def samo(volba, kol=10, limit_usd=5.0, minut=120):
    """Samostatný výzkum: AI hledá kolo po kole, dokud nenarazí na limit, nepotřebuje uživatele, nebo ji nezastavíte ■."""
    stav = _nacist_stav()
    stav["volba"] = volba
    _samo(stav, kol, limit_usd, minut)


def _samo(stav, kol=10, limit_usd=5.0, minut=120):
    zacatek, utraceno, kolo = time.time(), 0.0, 0
    print(f"Samostatný výzkum: nejvýš {kol} kol, ${limit_usd:.2f}, {minut} minut. Zastavíte ho tlačítkem ■ "
          "(rozpracované kolo se přeruší, uložené zůstane).")
    try:
        while kolo < kol:
            kolo += 1
            print(f"\n──── Kolo {kolo} z {kol} ────", flush=True)
            vypis = _poslat(SAMO_POKYN.format(kolo=kolo, kol=kol), stav, limit_usd=max(0.1, limit_usd - utraceno))
            zalohovat(tise=True)
            utraceno += vypis.usd or 0
            if SAMO_KONEC in vypis.cely:
                print("\nAI teď bez vás nepokračuje (důvod je v odpovědi výše).")
                break
            if vypis.chyby:
                print("\nKolo skončilo chybou, zastavuji.")
                break
            if utraceno >= limit_usd:
                print(f"\nDosažen limit útraty ${limit_usd:.2f}.")
                break
            if time.time() - zacatek > minut * 60:
                print(f"\nUplynulo {minut} minut.")
                break
    except KeyboardInterrupt:
        print("\nSamostatný výzkum zastaven.")
    zalohovat()
    print(f"Konec samostatného výzkumu: {kolo} kol, útrata asi ${utraceno:.2f}, "
          f"{round((time.time() - zacatek) / 60)} minut. Sporná místa vám AI ukáže, když napíšete "
          "„Ukaž mi sporná čtení“.")


def _poslat(zprava, stav, limit_usd=None):
    agent, model, ceny = MODELY[stav["volba"]]
    if _disk_pripojen() and os.path.isdir(os.path.join(DISK, "dokumenty")):
        _kopirovat(os.path.join(DISK, "dokumenty"), os.path.join(PROJEKT, "dokumenty"))   # co uživatel dal na Disk
    if agent == "claude":
        cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose", "--model", model,
               "--permission-mode", "acceptEdits", "--settings", json.dumps(NASTAVENI_CLAUDE)]
        sid = stav.get("claude")
        if sid and glob.glob(os.path.expanduser(f"~/.claude/projects/*/{sid}.jsonl")):
            cmd += ["--resume", sid]
        if limit_usd:
            cmd += ["--max-budget-usd", f"{limit_usd:.2f}"]
        vstup, zpracovat = zprava, _udalost_claude
    else:
        cmd = ["gemini", f"--prompt={zprava}", "--output-format", "stream-json", "--model", model,
               "--approval-mode", "yolo", "--skip-trust", "--policy", os.path.join(STAV, "zakazy.toml")]
        if stav.get("gemini"):
            cmd += ["--resume", "latest"]
        vstup, zpracovat = None, _udalost_gemini
    chyby = open(os.path.join(STAV, "chyby.log"), "w")
    proc = subprocess.Popen(cmd, cwd=PROJEKT, text=True, bufsize=1, stdout=subprocess.PIPE, stderr=chyby,
                            stdin=subprocess.PIPE if vstup else subprocess.DEVNULL)
    if vstup:
        proc.stdin.write(vstup)
        proc.stdin.close()
    vypis = _Vypis()
    try:
        for radek in proc.stdout:
            try:
                udalost = json.loads(radek)
            except ValueError:
                continue
            zpracovat(udalost, stav, vypis, ceny)
    finally:
        if proc.poll() is None:
            proc.terminate()
        proc.wait()
        chyby.close()
        vypis.dokoncit()
        _ulozit_stav(stav)
    if proc.returncode and not vypis.neco:
        print(f"Agent skončil s chybou ({proc.returncode}):")
        print(open(os.path.join(STAV, "chyby.log"), encoding="utf-8", errors="replace").read()[-2000:])
        vypis.chyby += 1
    return vypis


def _udalost_claude(u, stav, vypis, ceny):
    typ = u.get("type")
    if typ in ("system", "result") and u.get("session_id"):
        stav["claude"] = u["session_id"]
    if u.get("parent_tool_use_id"):   # práci pomocníků (subagentů) nevypisujeme
        return
    if typ == "assistant":
        for blok in u.get("message", {}).get("content", []):
            if blok.get("type") == "text":
                vypis.text(blok["text"])
            elif blok.get("type") == "tool_use":
                vypis.krok(_popis(blok.get("name"), blok.get("input") or {}))
    elif typ == "result":
        if u.get("is_error"):
            vypis.chyba(u.get("result") or u.get("subtype"))
        if u.get("total_cost_usd") is not None:   # s předplatným (OAuth) je cena jen orientační, nevypisujeme ji
            vypis.cena(u["total_cost_usd"], tisknout=bool(os.environ.get("ANTHROPIC_API_KEY")))


def _udalost_gemini(u, stav, vypis, ceny):
    typ = u.get("type")
    if typ == "message" and u.get("role") == "assistant":
        vypis.pridat(u.get("content", ""))
    elif typ == "tool_use":
        vypis.krok(_popis(u.get("tool_name"), u.get("parameters") or {}))
    elif typ == "error":
        vypis.chyba(u.get("message") or json.dumps(u, ensure_ascii=False))
    elif typ == "result":
        if u.get("status") == "error":
            vypis.chyba((u.get("error") or {}).get("message"))
        else:
            stav["gemini"] = True   # další zprávy navážou (--resume latest)
        s = u.get("stats") or {}
        vstup, cache = s.get("input_tokens", 0), s.get("cached", 0)
        vystup = max(0, s.get("total_tokens", 0) - vstup)   # odpověď i přemýšlení
        if vstup or vystup:
            vypis.cena(((vstup - cache) * ceny[0] + cache * ceny[2] + vystup * ceny[1]) / 1e6, odhad=True)


def _popis(nazev, vstup):
    for k in ("description", "command", "file_path", "absolute_path", "dir_path", "path", "url", "pattern",
              "query", "skill", "name", "prompt"):
        if vstup.get(k):
            return f"{nazev}: {str(vstup[k]).strip().splitlines()[0][:100]}"
    return str(nazev)


class _Vypis:
    """Vypisuje odpověď agenta: text jako Markdown (s obrázky z projektu), kroky jako šedé řádky."""
    OBRAZEK = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")

    def __init__(self):
        self.buffer, self.neco, self.cely, self.usd, self.chyby = "", False, "", None, 0

    def pridat(self, kus):
        self.buffer += kus

    def text(self, text):
        self.pridat(text)
        self._vypsat()

    def krok(self, popis):
        self._vypsat()
        print("  › " + popis, flush=True)

    def chyba(self, zprava):
        self._vypsat()
        zpravy = re.findall(r'message\\*"\s*:\s*\\*"([^"\\]+)', str(zprava))   # z vnořeného JSON jen text chyby
        print("⚠ " + (zpravy[-1] if zpravy else str(zprava)[:500]), flush=True)
        self.neco = True
        self.chyby += 1

    def cena(self, usd, odhad=False, tisknout=True):
        self._vypsat()
        self.usd = usd
        if tisknout:
            print(f"  (cena této odpovědi {'asi ' if odhad else ''}${usd:.3f})", flush=True)

    def dokoncit(self):
        self._vypsat()

    def _vypsat(self):
        text, self.buffer = self.buffer.strip(), ""
        if not text:
            return
        self.neco = True
        self.cely += text + "\n"
        try:
            from IPython.display import Image, Markdown, display
        except ImportError:
            print(text, flush=True)
            return
        konec = 0
        for m in self.OBRAZEK.finditer(text):
            cesta = m.group(2) if os.path.isabs(m.group(2)) else os.path.join(PROJEKT, m.group(2))
            if os.path.isfile(cesta):
                display(Markdown(text[konec:m.start()]))
                display(Image(filename=cesta, width=900))
                konec = m.end()
        display(Markdown(text[konec:]))


# ---------- pomocné ----------

def _nacist_stav():
    cesta = os.path.join(STAV, "relace.json")
    return json.load(open(cesta)) if os.path.exists(cesta) else {}


def _ulozit_stav(stav):
    os.makedirs(STAV, exist_ok=True)
    json.dump(stav, open(os.path.join(STAV, "relace.json"), "w"))


def _sh(cmd, cwd=None):
    r = subprocess.run(cmd, shell=isinstance(cmd, str), cwd=cwd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"Příkaz selhal: {cmd}\n{(r.stdout + r.stderr)[-3000:]}")
    return r.stdout
