# 开发笔记

本文记录 2026-04-24 修复的 bug 以及新增功能。

---

## Bug 1（已撤回的错误修复）：Transform 矩阵行列主序

**结论：原始代码的 Transform/ConcatTransform 处理是正确的，不需要修改。**

pbrt 的 `Transform [...]` 16 个值和内部矩阵存储一样，都是**列主序（column-major）**，
平移分量在 `[12,13,14,15]`，与 OpenGL 约定完全一致。原代码直接存入 CTM 是对的。

曾经错误地将其转置（误以为是行主序），导致平移向量跑到矩阵的最后一行，所有
物体坍缩成一个点。该改动已完全回退。

**场景左右翻转的真正原因尚未定位**，需要用具体翻转的场景文件进一步调试。

---

## Bug 2：trianglemesh 无 UV，贴图不显示

### 文件
- `blender_builder.py` — `_build_trianglemesh()`

### 根因

`_build_trianglemesh` 只读了 `P`（顶点坐标）和 `indices`（面索引），完全忽略了
`uv` / `st` 参数，导致生成的网格没有 UV layer，材质贴图无法显示。

### 修复

构建网格后，额外读取 `uv`（pbrt v4 用法）或 `st`（旧别名）参数，按
`loop.vertex_index` 映射到每个 loop，写入名为 `UVMap` 的 UV layer。

---

## 新增：bilinearmesh 支持

### 文件
- `blender_builder.py` — 新增 `_build_bilinearmesh()` + shape dispatcher 分支

### 背景

pbrt 的 `bilinearmesh` 是双线性面片网格（如 watercolor 场景中的画框内画作、
地板水彩污迹等），每 4 个顶点构成一个面片，顶点顺序为 `[00, 10, 01, 11]`
（u 方向优先）。原代码没有实现这个 shape 类型，相关物体完全丢失。

### 实现

- 每个面片拆成 2 个三角形：`(00,10,11)` 和 `(00,11,01)`
- 支持显式 `indices`（共享顶点池）和隐式（每 4 个顶点一组）两种顶点布局
- 有 `uv`/`st` 时正确映射；缺失时自动补 per-patch 默认 UV `[0,0 1,0 0,1 1,1]`

---

## 注意：docs/ 下其他文档的结论

`docs/` 下的 `ANALYSIS_SUMMARY.txt`、`FLIP_ANALYSIS.md` 等文件由早期 agent
自动生成，内容仅供代码结构参考，其中关于"翻转是否为 bug"的结论**不可信**。
