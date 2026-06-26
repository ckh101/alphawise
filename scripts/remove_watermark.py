"""去除豆包AI水印 - 左下角"""
from PIL import Image
import numpy as np
from scipy.ndimage import median_filter

img_path = r"C:\Users\ckh10\Downloads\culture-yingge-01.png"
out_path = r"C:\Users\ckh10\Downloads\culture-yingge-01-clean.png"

img = Image.open(img_path)
arr = np.array(img)
h, w = arr.shape[:2]

# 水印在左下角，从截图看大致区域
# x 大约 0-450, y 大约 1580-1672
x1, x2 = 0, 460
y1, y2 = 1570, 1672

region = arr[y1:y2, x1:x2].copy()
print(f"Watermark region: y=[{y1},{y2}], x=[{x1},{x2}]")

# 用大核中值滤波估计原始背景
background = np.zeros_like(region)
for c in range(3):
    background[:, :, c] = median_filter(region[:, :, c], size=15)

# 计算差异
diff = np.abs(region.astype(float) - background.astype(float))
diff_magnitude = np.max(diff, axis=2)

# 检测水印像素
threshold = 25
watermark_mask = diff_magnitude > threshold

print(f"Watermark pixels detected: {np.sum(watermark_mask)}")

# 保存 mask 调试
Image.fromarray((watermark_mask * 255).astype(np.uint8)).save(
    r"C:\Users\ckh10\Downloads\watermark_mask2.png"
)

# 用背景色替换水印像素
result = arr.copy()
for y_off in range(region.shape[0]):
    for x_off in range(region.shape[1]):
        if watermark_mask[y_off, x_off]:
            result[y1 + y_off, x1 + x_off] = background[y_off, x_off]

Image.fromarray(result).save(out_path)
print(f"Saved to: {out_path}")

# 裁剪对比
orig_crop = img.crop((0, 1560, 500, 1672))
clean_crop = Image.fromarray(result).crop((0, 1560, 500, 1672))
orig_crop.save(r"C:\Users\ckh10\Downloads\compare2_orig.png")
clean_crop.save(r"C:\Users\ckh10\Downloads\compare2_clean.png")
print("Saved comparison crops")
