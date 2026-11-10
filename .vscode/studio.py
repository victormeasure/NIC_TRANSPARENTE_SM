"""Integracao do projeto Simplicity Studio 5 com o VS Code.

Uso: python .vscode/studio.py <comando>

  new <nome> [c|h|ambos]   cria nome.c / nome.h no template do Studio, inclui o .h em
                           header.h e sincroniza (padrao: ambos)
  sync                     atualiza os makefiles do Studio e o compile_commands.json
                           com os .c da raiz do projeto
  build [Debug|Release]    sync + make (mesmo makefile/toolchain do Studio); sem argumento
                           (ou "auto") usa a unica configuracao existente ou pergunta
  clean [Debug|Release]    make clean
  compdb [Debug|Release]   so regenera o compile_commands.json (clangd)

Os makefiles sao do Studio ("Automatically-generated"): o sync apenas adianta o que o
Studio faria no proximo build dele (todo .c da raiz entra na compilacao), entao os dois
continuam compativeis. Componentes/defines/include paths novos continuam sendo feitos
no Studio; depois rode "sync" (ou "compdb") para o clangd enxergar.
"""
import datetime
import getpass
import glob
import json
import os
import re
import shlex
import subprocess
import sys

PROJECT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
STUDIO_DIR = "C:/SiliconLabs/SimplicityStudio/v5"
TOOLCHAIN_BIN = STUDIO_DIR + "/developer/toolchains/gnu_arm/12.2.rel1_2023.7/bin"
MSYS_BIN = STUDIO_DIR + "/support/common/build/msys/1.0/bin"
GCC = TOOLCHAIN_BIN + "/arm-none-eabi-gcc.exe"
BUILD_PREFIX = "GNU ARM v12.2.1 - "
AGGREGATE_HEADER = "header.h"  # header que inclui todos os modulos

MESES = ["jan.", "fev.", "mar.", "abr.", "mai.", "jun.",
         "jul.", "ago.", "set.", "out.", "nov.", "dez."]

MK_HEADER = (
    "################################################################################\n"
    "# Automatically-generated file. Do not edit!\n"
    "################################################################################\n"
    "\n"
)


def build_dir(config):
    return os.path.join(PROJECT_DIR, BUILD_PREFIX + config)


def build_dirs():
    return sorted(d for d in glob.glob(os.path.join(PROJECT_DIR, BUILD_PREFIX + "*"))
                  if os.path.isfile(os.path.join(d, "makefile")))


def read(path):
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        return f.read()


def write(path, text):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def root_sources():
    """Nomes (sem extensao) dos .c da raiz, na ordem que o Studio usa (ASCII)."""
    return sorted(os.path.splitext(n)[0] for n in os.listdir(PROJECT_DIR)
                  if n.endswith(".c") and os.path.isfile(os.path.join(PROJECT_DIR, n)))


# ---------------------------------------------------------------- makefiles

def sync_build_dir(bdir, names):
    """Poe os .c da raiz no subdir.mk e na linha de link do makefile. True se mudou."""
    subdir_mk = os.path.join(bdir, "subdir.mk")
    makefile = os.path.join(bdir, "makefile")
    text = read(subdir_mk)
    current = re.findall(r"^\.\./([^/\s]+)\.c\b", text, re.M)
    if current == names:
        return False

    m = re.search(r"^\t(arm-none-eabi-gcc .*?)\r?$", text, re.M)
    if not m:
        sys.exit(f"Regra de compilacao nao encontrada em {subdir_mk} (compile uma vez no Studio)")
    gcc_cmd = re.sub(r'-MF"[^"]*"', '-MF"{dep}"', m.group(1).replace("{", "{{").replace("}", "}}"))

    def var(name, fmt):
        return f"{name} += \\\n" + " \\\n".join(fmt.format(n) for n in names) + " \n\n"

    out = MK_HEADER
    out += "# Add inputs and outputs from these tool invocations to the build variables \n"
    out += var("C_SRCS", "../{}.c") + var("OBJS", "./{}.o") + var("C_DEPS", "./{}.d")
    out += "\n# Each subdirectory must supply rules for building sources it contributes\n"
    for n in names:
        out += (f"{n}.o: ../{n}.c subdir.mk\n"
                "\t@echo 'Building file: $<'\n"
                "\t@echo 'Invoking: GNU ARM C Compiler'\n"
                f"\t{gcc_cmd.format(dep=n + '.d')}\n"
                "\t@echo 'Finished building: $<'\n"
                "\t@echo ' '\n\n")
    write(subdir_mk, out)

    # A linha de link lista os objetos um a um: troca os da raiz ("./x.o")
    objs = "".join(f'"./{n}.o" ' for n in names)
    mk = read(makefile)

    def fix_link(match):
        line = re.sub(r'"\./[^/"]+\.o" ', "", match.group(0))
        return line.replace("-Wl,--start-group ", "-Wl,--start-group " + objs, 1)

    mk, n = re.subn(r"^\tarm-none-eabi-gcc .*-Wl,--start-group .*$", fix_link, mk, count=1, flags=re.M)
    if not n:
        sys.exit(f"Linha de link nao encontrada em {makefile}")
    write(makefile, mk)
    return True


# ------------------------------------------------------ compile_commands.json

RULE_RE = re.compile(r"^(\S+\.o): (\S+\.c) ")


def parse_subdir_mk(path, bdir):
    entries = []
    rule = None
    for line in read(path).splitlines():
        m = RULE_RE.match(line)
        if m:
            rule = m.groups()
            continue
        stripped = line.strip()
        if rule and stripped.startswith("arm-none-eabi-gcc"):
            obj, src = rule
            args = shlex.split(stripped.replace("\\", "/"))
            args[0] = GCC
            args = [a.replace("$<", src).replace("$@", obj) for a in args]
            entries.append({
                "directory": bdir.replace("\\", "/"),
                "file": os.path.normpath(os.path.join(bdir, src)).replace("\\", "/"),
                "arguments": args,
            })
            rule = None
    return entries


def compdb(config="Debug"):
    bdir = build_dir(config)
    if not os.path.isdir(bdir):
        sys.exit(f"Pasta de build nao encontrada: {bdir} (compile uma vez no Studio)")
    entries = []
    for root, _, files in os.walk(bdir):
        if "subdir.mk" in files:
            entries += parse_subdir_mk(os.path.join(root, "subdir.mk"), bdir)
    entries.sort(key=lambda e: e["file"])
    out = os.path.join(PROJECT_DIR, "compile_commands.json")
    text = json.dumps(entries, indent=2)
    if os.path.isfile(out) and read(out) == text:
        return
    write(out, text)
    print(f"compile_commands.json: {len(entries)} entradas ({config})")


def sync(config="Debug"):
    dirs = build_dirs()
    if not dirs:
        sys.exit("Nenhuma pasta de build encontrada (compile uma vez no Studio)")
    names = root_sources()
    for bdir in dirs:
        if sync_build_dir(bdir, names):
            print(f"Makefiles atualizados: {os.path.basename(bdir)} ({len(names)} fontes na raiz)")
    if not os.path.isdir(build_dir(config)):
        config = os.path.basename(dirs[0])[len(BUILD_PREFIX):]
    compdb(config)


# ------------------------------------------------------------------- build

def choose_config():
    """Unica configuracao existente; se houver mais de uma (Debug e Release), pergunta."""
    configs = [os.path.basename(d)[len(BUILD_PREFIX):] for d in build_dirs()]
    if not configs:
        sys.exit("Nenhuma pasta de build encontrada (compile uma vez no Studio)")
    if len(configs) == 1:
        return configs[0]
    for i, c in enumerate(configs, 1):
        print(f"  {i}) {c}")
    while True:
        try:
            ans = input(f"Compilar qual? [1-{len(configs)}, Enter = {configs[0]}]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            sys.exit("\nCancelado")
        if not ans:
            return configs[0]
        if ans.isdigit() and 1 <= int(ans) <= len(configs):
            return configs[int(ans) - 1]
        match = [c for c in configs if c.lower().startswith(ans)]
        if len(match) == 1:
            return match[0]


def make(config, *targets):
    bdir = build_dir(config)
    if not os.path.isfile(os.path.join(bdir, "makefile")):
        sys.exit(f"Makefile nao encontrado em {bdir} (compile uma vez no Studio)")
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([os.path.normpath(TOOLCHAIN_BIN), os.path.normpath(MSYS_BIN), env["PATH"]])
    cmd = [os.path.normpath(MSYS_BIN + "/make.exe"), f"-j{os.cpu_count() or 4}", *targets]
    return subprocess.call(cmd, cwd=bdir, env=env)


# ------------------------------------------------------------ novo arquivo

def file_banner(filename):
    d = datetime.date.today()
    return ("/*\n"
            f" * {filename}\n"
            " *\n"
            f" *  Created on: {d.day} de {MESES[d.month - 1]} de {d.year}\n"
            f" *      Author: {getpass.getuser()}\n"
            " */\n\n")


def add_to_aggregate_header(name):
    path = os.path.join(PROJECT_DIR, AGGREGATE_HEADER)
    if not os.path.isfile(path):
        return
    text = read(path)
    include = f'#include "{name}.h"'
    idx = text.rfind("#endif")
    if include in text or idx < 0:
        return
    eol = "\r\n" if "\r\n" in text else "\n"
    head = text[:idx]
    body = head.rstrip()
    # 1o modulo: separa dos includes de sistema com 2 linhas em branco; depois, empilha
    sep = eol * 3 if head[len(body):].count("\n") >= 3 else eol
    write(path, body + sep + include + eol * 2 + text[idx:])
    print(f"{AGGREGATE_HEADER}: adicionado {include}")


def new(name, kind="ambos"):
    name = os.path.splitext(name.strip())[0]
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        sys.exit(f"Nome invalido: '{name}' (use letras, numeros e _)")
    kind = kind.lower()
    exts = {"c": ["c"], "h": ["h"], "ambos": ["h", "c"]}.get(kind)
    if not exts:
        sys.exit("Tipo deve ser c, h ou ambos")
    for ext in exts:
        if os.path.exists(os.path.join(PROJECT_DIR, f"{name}.{ext}")):
            sys.exit(f"{name}.{ext} ja existe")

    created = []
    for ext in exts:
        filename = f"{name}.{ext}"
        if ext == "h":
            guard = re.sub(r"[^A-Z0-9]", "_", name.upper()) + "_H_"
            body = f"#ifndef {guard}\n#define {guard}\n\n\n\n#endif /* {guard} */\n"
        else:
            body = f'#include "{AGGREGATE_HEADER}"\n\n'
        with open(os.path.join(PROJECT_DIR, filename), "w", encoding="utf-8", newline="\n") as f:
            f.write(file_banner(filename) + body)
        created.append(filename)
        print(f"Criado {filename}")

    if "h" in exts:
        add_to_aggregate_header(name)
    if "c" in exts:
        sync()
    if os.environ.get("TERM_PROGRAM") == "vscode":
        subprocess.call("code -r " + " ".join(f'"{f}"' for f in created), cwd=PROJECT_DIR, shell=True)


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else ""
    rest = [a for a in args[1:] if a]
    if cmd == "new" and rest:
        new(*rest[:2])
    elif cmd == "sync":
        sync(*rest[:1])
    elif cmd == "compdb":
        compdb(*rest[:1])
    elif cmd == "build":
        config = rest[0] if rest else "auto"
        if config == "auto":
            config = choose_config()
        print(f"== Build {config} ==", flush=True)
        sync(config)
        sys.exit(make(config, "all"))
    elif cmd == "clean":
        sys.exit(make(rest[0] if rest else "Debug", "clean"))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
