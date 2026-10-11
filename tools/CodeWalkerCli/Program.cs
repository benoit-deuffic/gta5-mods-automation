using System;
using System.IO;
using System.Xml;
using CodeWalker.GameFiles;

// CLI contract consumed by scripts/codewalker_convert.py:
//   codewalker-cli convert --in <xml_dir> --out <out_dir> --name <model_name>
// Converts <model_name>.ydd.xml / .yft.xml / .ytd.xml (CodeWalker-XML, as
// produced by scripts/blender_rig_export.py's Sollumz CWXML export target)
// into real binary RAGE files. Verified to work on Linux/.NET 8 against
// CodeWalker.Core (netstandard2.0) -- see docs/architecture-decisions.md
// point 10.
//
// .ymt.xml is INTENTIONALLY NOT handled here yet: scripts/ymt_builder.py's
// current output does not match the real CPedVariationInfo PSO struct
// (verified against CodeWalker.Core/GameFiles/MetaTypes/MetaTypes.cs --
// missing bHasTexVariations/bHasDrawblVariations/bHasLowLODs/bIsSuperLOD,
// wrong nesting for aComponentData3/aDrawblData3, invented fields like
// pedXml_availTexs). Converting it now would silently produce a corrupt
// .ymt. This CLI reports .ymt as "skipped: schema not yet implemented"
// rather than emit wrong bytes.

if (args.Length == 0 || args[0] != "convert")
{
    Console.Error.WriteLine("Usage: codewalker-cli convert --in <xml_dir> --out <out_dir> --name <model_name>");
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

var ymtPath = Path.Combine(xmlDir, modelName + ".ymt.xml");
if (File.Exists(ymtPath))
{
    Console.Error.WriteLine(
        $"[SKIP] {ymtPath}: .ymt conversion not implemented yet -- " +
        "scripts/ymt_builder.py's schema does not match the real CPedVariationInfo " +
        "PSO struct (see docs/architecture-decisions.md point 10). Not converting " +
        "to avoid producing a corrupt .ymt.");
    skipped++;
}

Console.WriteLine($"Done: {converted} converted, {skipped} skipped, {failed} failed.");
return failed > 0 ? 1 : 0;
