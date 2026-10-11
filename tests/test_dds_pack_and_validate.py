from pathlib import Path

import pytest

from scripts import dds_pack_and_validate as dpv

FIXTURE_YTD_XML = """<?xml version="1.0" encoding="UTF-8"?>
<TextureDictionary>
 <TextureNameHash />
 <Unknown28 value="0" />
 <Textures>
  <Item>
   <Unk32 value="128" />
   <Usage>DIFFUSE</Usage>
   <UsageFlags>X32, X64, X128, X512, UNK24</UsageFlags>
   <ExtraFlags value="0" />
   <Width value="512" />
   <Height value="512" />
   <MipLevels value="8" />
   <Format>D3DFMT_DXT1</Format>
   <FileName>uppr_diff_000_a_whi.dds</FileName>
  </Item>
  <Item>
   <Unk32 value="128" />
   <Usage>SPECULAR</Usage>
   <UsageFlags>X32</UsageFlags>
   <ExtraFlags value="0" />
   <Width value="256" />
   <Height value="256" />
   <MipLevels value="7" />
   <Format>D3DFMT_DXT5</Format>
   <FileName>uppr_spec_000.dds</FileName>
  </Item>
 </Textures>
</TextureDictionary>
"""


def test_read_texture_format_finds_matching_item(tmp_path: Path):
    xml_path = tmp_path / "a_f_y_business_02.ytd.xml"
    xml_path.write_text(FIXTURE_YTD_XML)

    fmt = dpv.read_texture_format(xml_path, "uppr_diff_000_a_whi")
    assert fmt == {
        "width": 512, "height": 512, "mip_levels": 8,
        "format": "D3DFMT_DXT1", "filename": "uppr_diff_000_a_whi.dds",
    }


def test_read_texture_format_second_item(tmp_path: Path):
    xml_path = tmp_path / "a_f_y_business_02.ytd.xml"
    xml_path.write_text(FIXTURE_YTD_XML)

    fmt = dpv.read_texture_format(xml_path, "uppr_spec_000")
    assert fmt["format"] == "D3DFMT_DXT5"
    assert fmt["width"] == 256


def test_read_texture_format_missing_raises(tmp_path: Path):
    xml_path = tmp_path / "a_f_y_business_02.ytd.xml"
    xml_path.write_text(FIXTURE_YTD_XML)

    with pytest.raises(RuntimeError, match="not found"):
        dpv.read_texture_format(xml_path, "does_not_exist")


def test_pack_dds_rejects_unmapped_format(tmp_path: Path):
    fmt_info = {"width": 4, "height": 4, "format": "D3DFMT_UNKNOWN_WEIRD"}
    with pytest.raises(RuntimeError, match="Unsupported"):
        dpv.pack_dds(tmp_path / "src.png", tmp_path / "dest.dds", fmt_info)
