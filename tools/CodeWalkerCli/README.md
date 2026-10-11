# CodeWalker CLI — XML → binary RAGE converter

Thin .NET 8 console wrapper around `CodeWalker.Core` (netstandard2.0),
exposing the CLI contract `scripts/codewalker_convert.py` expects:

```
codewalker-cli convert --in <xml_dir> --out <out_dir> --name <model_name>
```

Converts `<model_name>.ytd.xml`, `.ydd.xml`, `.yft.xml` (CodeWalker-XML, as
produced by `scripts/blender_rig_export.py`'s Sollumz CWXML export target)
into real binary RAGE files. **Verified to run on Linux/.NET 8** — see
`docs/architecture-decisions.md` point 10. This directly contradicts the
earlier assumption ("Linux build not yet proven") in the `gtav-ped-modding`
skill's toolchain notes; it has now been proven.

`.ymt.xml` is intentionally **not** converted yet: `scripts/ymt_builder.py`'s
current output does not match the real `CPedVariationInfo` PSO struct
(verified against `CodeWalker.Core/GameFiles/MetaTypes/MetaTypes.cs`). The
CLI reports it as skipped rather than emit a corrupt `.ymt` — see
`docs/architecture-decisions.md` point 10 for the exact field-level diff and
what needs to be rewritten before this works.

## Build

```bash
scripts/fetch_codewalker.sh      # clones CodeWalker into third_party/ (gitignored)
dotnet publish tools/CodeWalkerCli -c Release -r linux-x64 \
  --self-contained true -p:PublishSingleFile=true \
  -o tools/CodeWalkerCli/publish
```

Produces a single self-contained executable at
`tools/CodeWalkerCli/publish/codewalker-cli` (~70MB, includes the .NET
runtime — no `dotnet` needed at runtime). Point the pipeline at it:

```bash
export CODEWALKER_CLI_PATH="$(pwd)/tools/CodeWalkerCli/publish/codewalker-cli"
```

On Windows/bora, build without `-r linux-x64 --self-contained true` (use the
framework-dependent default, or `-r win-x64` for a self-contained .exe).

## Why not vendored

CodeWalker's GitHub repo has no explicit OSS license file, so its source is
fetched at build time into `third_party/CodeWalker` (gitignored) rather than
committed into this repository — same "never bundle what you don't own"
principle as the Rockstar game assets, applied to an ambiguously-licensed
third-party tool.
