using System;
using System.IO;
using System.Xml;
using CodeWalker.GameFiles;

// CLI contract consumed by scripts/codewalker_convert.py:
//   codewalker-cli convert --in <xml_dir> --out <out_dir> --name <model_name>
// Converts <model_name>.ydd.xml / .yft.xml / .ytd.xml / .ymt.xml
// (CodeWalker-XML, as produced by scripts/blender_rig_export.py's Sollumz
// CWXML export target and scripts/ymt_builder.py) into real binary RAGE
// files. Verified to work on Linux/.NET 8 against CodeWalker.Core
// (netstandard2.0) -- see docs/architecture-decisions.md points 10-11.
//
// .ymt uses MetaFormat.RSC (GetRSCData -> GetMeta -> generic PSO/structure
// traversal), NOT one of the dedicated Ytd/Ydd/Yft cases -- CodeWalker's own
// XmlMeta.GetXMLFormat() falls through to RSC for any "*.xml" that doesn't
// match one of its special-cased double extensions, and ".ymt.xml" isn't one
// of them. scripts/ymt_builder.py's schema was fixed to match the real
// CPedVariationInfo PSO struct (point 11) before this was enabled.

if (args.Length == 0)
{
    Console.Error.WriteLine("Usage: codewalker-cli convert --in <xml_dir> --out <out_dir> --name <model_name>");
    Console.Error.WriteLine("       codewalker-cli inspect <binary_file> [--type ymt|ydd|yft|ytd]");
    Console.Error.WriteLine("       codewalker-cli export-xml --in <binary_file> --out <out_dir>");
    return 2;
}

if (args[0] == "inspect")
{
    return RunInspect(args);
}

if (args[0] == "export-xml")
{
    return RunExportXml(args);
}

if (args[0] != "convert")
{
    Console.Error.WriteLine("Usage: codewalker-cli convert --in <xml_dir> --out <out_dir> --name <model_name>");
    Console.Error.WriteLine("       codewalker-cli inspect <binary_file> [--type ymt|ydd|yft|ytd]");
    Console.Error.WriteLine("       codewalker-cli export-xml --in <binary_file> --out <out_dir>");
    return 2;
}

string? xmlDir = null, outDir = null, modelName = null;
for (int i = 1; i < args.Length; i++)
{
    switch (args[i])
    {
        case "--in": xmlDir = args[++i]; break;
        case "--out": outDir = args[++i]; break;
        case "--name": modelName = args[++i]; break;
        default:
            Console.Error.WriteLine($"Unknown argument: {args[i]}");
            return 2;
    }
}

if (xmlDir == null || outDir == null || modelName == null)
{
    Console.Error.WriteLine("Missing required --in/--out/--name argument.");
    return 2;
}

Directory.CreateDirectory(outDir);

var conversions = new (string suffix, MetaFormat format, bool needsFolder)[]
{
    (".ytd.xml", MetaFormat.Ytd, true),
    (".ydd.xml", MetaFormat.Ydd, true),
    (".yft.xml", MetaFormat.Yft, true),
    (".ymt.xml", MetaFormat.RSC, false),
};

int converted = 0, skipped = 0, failed = 0;

foreach (var (suffix, format, needsFolder) in conversions)
{
    var xmlPath = Path.Combine(xmlDir, modelName + suffix);
    if (!File.Exists(xmlPath))
    {
        continue; // not every ped produces every component; not an error
    }

    try
    {
        var doc = new XmlDocument();
        doc.Load(xmlPath);
        byte[]? data = XmlMeta.GetData(doc, format, needsFolder ? xmlDir : "");
        if (data == null)
        {
            Console.Error.WriteLine($"[FAIL] {xmlPath}: converter returned null (malformed/empty XML for {format}).");
            failed++;
            continue;
        }
        var outExt = suffix.Substring(0, suffix.Length - ".xml".Length); // ".ytd.xml" -> ".ytd"
        var outPath = Path.Combine(outDir, modelName + outExt);
        File.WriteAllBytes(outPath, data);
        Console.WriteLine($"[OK] {xmlPath} -> {outPath} ({data.Length} bytes)");
        converted++;
    }
    catch (Exception ex)
    {
        Console.Error.WriteLine($"[FAIL] {xmlPath}: {ex.GetType().Name}: {ex.Message}");
        failed++;
    }
}

Console.WriteLine($"Done: {converted} converted, {skipped} skipped, {failed} failed.");
return failed > 0 ? 1 : 0;

// Round-trip sanity check: loads a produced binary back through CodeWalker's
// own reader and reports the PSO/Meta structure blocks it recognizes. Used
// to verify scripts/ymt_builder.py's output is actually well-formed (not
// just "didn't throw during conversion") -- see docs/architecture-decisions.md
// point 11. Currently only .ymt (Meta/PSO-based) is meaningfully inspectable
// this way; .ydd/.yft/.ytd are RSC drawable/texture dictionaries with a
// different internal layout.
static int RunInspect(string[] args)
{
    if (args.Length < 2)
    {
        Console.Error.WriteLine("Usage: codewalker-cli inspect <binary_file>");
        return 2;
    }
    var path = args[1];
    if (!File.Exists(path))
    {
        Console.Error.WriteLine($"File not found: {path}");
        return 1;
    }

    var bytes = File.ReadAllBytes(path);
    Console.WriteLine($"Loaded {bytes.Length} bytes from {path}");

    try
    {
        var ymt = new YmtFile();
        ymt.Load(bytes);

        Console.WriteLine($"Meta != null: {ymt.Meta != null}");
        if (ymt.Meta?.DataBlocks != null)
        {
            Console.WriteLine($"DataBlocks.Count = {ymt.Meta.DataBlocks.Count}");
            for (int i = 0; i < ymt.Meta.DataBlocks.Count; i++)
            {
                var block = ymt.Meta.DataBlocks[i];
                Console.WriteLine($"  Block[{i}]: StructureNameHash={block.StructureNameHash}, DataLength={block.Data?.Length ?? 0}");
            }
            if (ymt.Meta.DataBlocks.Count == 0)
            {
                Console.Error.WriteLine("[WARN] No data blocks found -- file may be empty/malformed.");
                return 1;
            }
        }
        else
        {
            Console.Error.WriteLine("[FAIL] Meta or Meta.DataBlocks is null -- not a valid PSO/Meta (RSC) file.");
            return 1;
        }
    }
    catch (Exception ex)
    {
        Console.Error.WriteLine($"[FAIL] {ex.GetType().Name}: {ex.Message}");
        return 1;
    }

    Console.WriteLine("OK: round-trip load succeeded, structure blocks recognized.");
    return 0;
}

// Converts a loose RPF-resource binary file (.ydd/.yft/.ytd/.ybn/.ycd/...,
// the self-contained RSC7 format produced by CodeWalker/OpenIV export --
// NOT a raw RPF archive) into CodeWalker-XML, the format Sollumz's import
// operator also accepts (sollumz_operators.py ImportAssetsOperatorImpl
// filter_glob includes "*.ydd.xml;*.yft.xml;*.ytd.xml;..."). This exists
// because Sollumz's binary import path requires PyMateria, which is
// Windows-only (see dependencies.py: `sys.platform == "win32"`) and is NOT
// available on Linux/WSL2 -- going through XML sidesteps that requirement
// entirely, since XML import uses Sollumz's own pure-Python/CWXML parser.
static int RunExportXml(string[] args)
{
    string? inPath = null, outDir = null;
    for (int i = 1; i < args.Length; i++)
    {
        switch (args[i])
        {
            case "--in": inPath = args[++i]; break;
            case "--out": outDir = args[++i]; break;
            default:
                Console.Error.WriteLine($"Unknown argument: {args[i]}");
                return 2;
        }
    }
    if (inPath == null || outDir == null)
    {
        Console.Error.WriteLine("Usage: codewalker-cli export-xml --in <binary_file> --out <out_dir>");
        return 2;
    }
    if (!File.Exists(inPath))
    {
        Console.Error.WriteLine($"File not found: {inPath}");
        return 1;
    }

    Directory.CreateDirectory(outDir);
    var bytes = File.ReadAllBytes(inPath);
    var ext = Path.GetExtension(inPath).ToLowerInvariant();
    var baseName = Path.GetFileName(inPath); // e.g. "a_f_y_business_02.yft" -- GameFile.Name
                                              // isn't auto-populated by GetResourceFile<T>()
                                              // (that only happens via RPF directory metadata,
                                              // which loose/extracted files don't have), and
                                              // MetaXml.GetXml()'s output filename is derived
                                              // from it -- set it explicitly or every export
                                              // lands in a file literally named ".xml".

    try
    {
        string xml;
        string filename;
        switch (ext)
        {
            case ".ydd":
            {
                var f = RpfFile.GetResourceFile<YddFile>(bytes);
                f.Name = baseName;
                xml = MetaXml.GetXml(f, out filename, outDir);
                break;
            }
            case ".yft":
            {
                var f = RpfFile.GetResourceFile<YftFile>(bytes);
                f.Name = baseName;
                xml = MetaXml.GetXml(f, out filename, outDir);
                break;
            }
            case ".ytd":
            {
                var f = RpfFile.GetResourceFile<YtdFile>(bytes);
                f.Name = baseName;
                xml = MetaXml.GetXml(f, out filename, outDir);
                break;
            }
            case ".ydr":
            {
                var f = RpfFile.GetResourceFile<YdrFile>(bytes);
                f.Name = baseName;
                xml = MetaXml.GetXml(f, out filename, outDir);
                break;
            }
            default:
                Console.Error.WriteLine($"[FAIL] Unsupported extension for export-xml: {ext} (supported: .ydd/.yft/.ytd/.ydr)");
                return 1;
        }

        if (string.IsNullOrEmpty(xml))
        {
            Console.Error.WriteLine($"[FAIL] {inPath}: CodeWalker returned empty XML (file may be corrupt/unsupported variant).");
            return 1;
        }

        var outPath = Path.Combine(outDir, filename);
        File.WriteAllText(outPath, xml);
        Console.WriteLine($"[OK] {inPath} -> {outPath} ({xml.Length} chars)");
        return 0;
    }
    catch (Exception ex)
    {
        Console.Error.WriteLine($"[FAIL] {inPath}: {ex.GetType().Name}: {ex.Message}");
        return 1;
    }
}
