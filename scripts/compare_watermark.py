"""裁剪对比区域"""
from PIL import Image

orig = Image.open(r"C:\Users\ckh10\Downloads\culture-yingge-01.png")
clean = Image.open(r"C:\Users\ckh10\Downloads\culture-yingge-01-clean.png")

# 裁剪右下角水印区域
box = (1900, 1560, 2508, 1672)
orig.crop(box).save(r"C:\Users\ckh10\Downloads\compare_orig.png")
clean.crop(box).save(r"C:\Users\ckh10\Downloads\compare_clean.png")
print("Saved compare_orig.png and compare_clean.png")
