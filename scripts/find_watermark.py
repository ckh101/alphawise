"""把整张图分成网格，截取每个角落区域来找水印"""
from PIL import Image

img = Image.open(r"C:\Users\ckh10\Downloads\culture-yingge-01.png")
w, h = img.size

regions = {
    "top_left": (0, 0, 500, 150),
    "top_right": (w - 500, 0, w, 150),
    "bottom_left": (0, h - 150, 500, h),
    "bottom_right": (w - 500, h - 150, w, h),
    "bottom_center": (w // 2 - 250, h - 150, w // 2 + 250, h),
}

for name, box in regions.items():
    crop = img.crop(box)
    crop.save(fr"C:\Users\ckh10\Downloads\corner_{name}.png")
    print(f"Saved corner_{name}.png")
