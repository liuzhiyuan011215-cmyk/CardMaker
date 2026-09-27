"""生成开发用的 macOS 启动器 CardMaker.app（双击即可打开界面，不用开终端）。

它只是一个很薄的外壳，实际运行的是项目里 .venv 的 Python，所以只能在这台电脑上用。
正式发布用第九阶段的 PyInstaller 打包。

运行：.venv/bin/python scripts/make_mac_launcher.py
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
ICON_PNG = ROOT / "src" / "cardmaker" / "gui" / "assets" / "icon.png"
APP = ROOT / "CardMaker.app"

INFO_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>CardMaker</string>
  <key>CFBundleDisplayName</key><string>CardMaker</string>
  <key>CFBundleIdentifier</key><string>edu.graduation.cardmaker.dev</string>
  <key>CFBundleVersion</key><string>0.1.0</string>
  <key>CFBundleShortVersionString</key><string>0.1.0</string>
  <key>CFBundleExecutable</key><string>CardMaker</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
</dict></plist>
"""

LAUNCHER = """#!/bin/bash
# 开发用启动器：用项目里的 Python 环境启动 CardMaker 界面
exec "{python}" -m cardmaker.gui
"""


def draw_icon(size: int = 1024) -> Image.Image:
    """蓝色圆角底 + 两张叠放的白色卡片，前面那张写一个「가」。"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bg = Image.new("RGBA", (size, size))
    top, bottom = (59, 130, 246), (29, 78, 216)
    for y in range(size):  # 竖直渐变
        t = y / size
        ImageDraw.Draw(bg).line([(0, y), (size, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([40, 40, size - 40, size - 40], radius=220, fill=255)
    img.paste(bg, (0, 0), mask)

    back = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(back).rounded_rectangle([300, 210, 780, 760], radius=60, fill=(255, 255, 255, 110))
    img.alpha_composite(back.rotate(-10, center=(540, 485), resample=Image.Resampling.BICUBIC))

    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([250, 290, 730, 840], radius=60, fill=(0, 0, 0, 90))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(24)))
    front = ImageDraw.Draw(img)
    front.rounded_rectangle([240, 270, 720, 820], radius=60, fill=(255, 255, 255, 255))
    font = ImageFont.truetype("/System/Library/Fonts/AppleSDGothicNeo.ttc", 300, index=6)  # 粗体
    front.text((480, 520), "가", font=font, fill=(29, 78, 216), anchor="mm")
    front.rounded_rectangle([330, 700, 630, 730], radius=15, fill=(191, 219, 254))
    return img


def build_icns(png: Path, target: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "AppIcon.iconset"
        iconset.mkdir()
        source = Image.open(png)
        for base in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                px = base * scale
                name = f"icon_{base}x{base}{'@2x' if scale == 2 else ''}.png"
                source.resize((px, px), Image.Resampling.LANCZOS).save(iconset / name)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(target)], check=True)


def main() -> None:
    ICON_PNG.parent.mkdir(parents=True, exist_ok=True)
    if not ICON_PNG.exists():
        draw_icon().save(ICON_PNG)
    if APP.exists():
        shutil.rmtree(APP)
    (APP / "Contents" / "MacOS").mkdir(parents=True)
    (APP / "Contents" / "Resources").mkdir(parents=True)
    (APP / "Contents" / "Info.plist").write_text(INFO_PLIST, encoding="utf-8")
    launcher = APP / "Contents" / "MacOS" / "CardMaker"
    launcher.write_text(LAUNCHER.format(python=ROOT / ".venv" / "bin" / "python"), encoding="utf-8")
    launcher.chmod(0o755)
    build_icns(ICON_PNG, APP / "Contents" / "Resources" / "AppIcon.icns")
    print(f"已生成 {APP}")


if __name__ == "__main__":
    main()
