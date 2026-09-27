# -*- coding: utf-8 -*-
"""镜头构图的几何：世界坐标 -> 屏幕位置，以及"让这些单位全落在可见区中间"的镜头目标。

为什么要有它（09-18 实机）：
交战时镜头偏上方，看不全战场，要往下挪，让所有参战单位都落在画面里。
对局画面里打仗的单位挤在画面下半截、一部分压在底部操作栏后面。

原来的做法是"镜头目标对准交火的重心"。那不够，两个原因（都是 09-18 标定出来的）：
  1. **透视**：俯角 56° 看下去，靠镜头这边（南边）的一片地在屏幕上占的地方比远处大得多。
     一个上下对称的外框，南半边会被推到屏幕很下面 —— 正好是操作栏的位置。
  2. **可见区不是整个屏幕**：上面一条顶栏，下面约 27% 是操作栏（小地图、头像、物品）。

所以这里真的按投影算：把参战单位投到屏幕上，找一个镜头目标，让它们在可见区里居中；
装不下就告诉调用方（导播会拉到全景再算一次）。

投影 = 游戏自己画世界用的那一套（09-18 头顶气泡那条线从游戏渲染时的投影参数量出来的，
实测；1920×1080 客户区、默认对战镜头、视野 70）：
  * 世界画在**视口 (0, 35) 1920×811**（顶栏和操作台之间）；
  * 投影矩阵 _11 = 1.822、_22 = 3.239（宽高比按 16:9 算，近 100 远 5100）——
    可视口是 1920×811，于是**横纵像素不等比**：
    横向焦距 = 视口宽/2 × _11 ≈ 1749 像素，纵向焦距 = 视口高/2 × _22 ≈ 1313 像素；
  * 投影中心 = 视口中心 (960, 35 + 405.5) —— 屏幕高度 0.408；
  * VIEW 就是 look-at：右 +x、上 (0, sin, cos)、前 (0, cos, -sin)，和下面 project 的相机轴一样。

⚠ 更正（09-18 晚）：原来这里是"视野 70 = 水平视野 + 正方形像素（焦距 1371）+ 中心 0.42"，
  对着一张截图标定的 —— 标定点（二矿大厅，离屏幕中心横向 80 像素）离中心太近，
  横向差 1749/1371 = 1.28 倍在那里只有二十来像素，显不出来。
  复核（09-18 晚，5 号实例，同一时刻读镜头 + 单位 + 截图，每个点用 GetLocationZ 取地面高度；
  距离 2600 一张、3200 一张，16 座建筑）：新模型每座都画在建筑正中；旧模型离中心越远偏得越多，
  伐木场（相对镜头目标 -886, -553，比目标高 92）截图上 x≈252，新 260、旧 411；
  铁匠铺（-1111, -793，距离 3200）截图 245，新 244、旧 399。数钉在 tests/test_director_frame.py 的 Calibration。
  —— 旧模型把横向能装下的范围多估了 28%：中景东西以为装得下 ±1674，实际 ±1312。
⚠ 只在导播默认朝向（正北，yaw 90）下成立 —— 导播从不转镜头（环绕 09-15 就关了）。
⚠ 单位坐标没有 z：这里默认和镜头目标一样高（dz=0）。高地/浅水差 100，纵向就差约 25 像素，
  可见区上下各留的余量吃掉的是这一部分（要准就传 dz = 地面高度 - 镜头目标 z）。
"""
import math

REF_W, REF_H = 1920.0, 1080.0     # 客户区（启动器 --client 0,0,1920,1080 = 渲染分辨率）
# 游戏画世界的视口（从渲染参数量的）：顶栏下沿 35 到操作台 846
VIEW_X, VIEW_Y, VIEW_W, VIEW_H = 0.0, 35.0, 1920.0, 811.0
# 投影矩阵两项（从游戏渲染时的投影参数量出来的，视野 70 时）
PROJ_11, PROJ_22 = 1.822, 3.239
FOV_REF = 70.0
FOCAL_X = VIEW_W / 2.0 * PROJ_11                   # ≈ 1749 像素
FOCAL_Y = VIEW_H / 2.0 * PROJ_22                   # ≈ 1313 像素
CENTER_U = (VIEW_X + VIEW_W / 2.0) / REF_W         # 0.5
CENTER_V = (VIEW_Y + VIEW_H / 2.0) / REF_H         # ≈ 0.408：投影中心在屏幕高度的比例
# 可见区（屏幕比例）：顶栏下沿约 0.035，操作栏上沿约 0.73（截图量的）。
# 投的是单位**脚下**那一点，人和血条往上长约 0.06 —— 所以上沿留得多、下沿留得少。
# 09-18 晚复核：这几个是屏幕比例、和焦距无关，不用跟着改。顶栏下沿 = 视口上沿 35/1080；
# 视口画到 846（0.783），但操作栏的石头边在 0.73~0.77 就把它盖住了。
VIS_TOP, VIS_BOT = 0.10, 0.70
VIS_L, VIS_R = 0.04, 0.96


def project(dx, dy, dist, pitch_deg=304.0, fov_deg=70.0, dz=0.0):
    """相对镜头目标 (dx, dy, dz) 的一点 -> 屏幕 (u, v)，都是 0..1 的比例；在镜头后面返回 None。

    fov_deg 不是 70 时焦距按 tan(视野/2) 反比缩放 —— 只实测过 70（导播从不改视野）。
    """
    down = math.radians(360.0 - pitch_deg) if pitch_deg > 180.0 else math.radians(-pitch_deg)
    c, s = math.cos(down), math.sin(down)
    # 眼睛在目标南边 dist·cos、上方 dist·sin；朝向 f=(0,c,-s)，右 r=(1,0,0)，上 u=(0,s,c)
    py = dy + dist * c
    pz = dz - dist * s
    zc = py * c - pz * s
    if zc <= 1.0:
        return None
    yc = py * s + pz * c
    k = 1.0 if fov_deg == FOV_REF else \
        math.tan(math.radians(FOV_REF) / 2.0) / math.tan(math.radians(fov_deg) / 2.0)
    return (CENTER_U + dx / zc * FOCAL_X * k / REF_W,
            CENTER_V - yc / zc * FOCAL_Y * k / REF_H)


def _span(points, tx, ty, dist, pitch, fov):
    uv = [project(x - tx, y - ty, dist, pitch, fov) for x, y in points]
    uv = [p for p in uv if p]
    if not uv:
        return None
    us = [p[0] for p in uv]
    vs = [p[1] for p in uv]
    return min(us), max(us), min(vs), max(vs)


def _bisect(fn, lo, hi, iters=40):
    """fn 单调，找 fn(t) = 0。两端同号时返回更接近 0 的那一端。"""
    flo, fhi = fn(lo), fn(hi)
    if flo is None or fhi is None:
        return (lo + hi) / 2.0
    if (flo > 0) == (fhi > 0):
        return lo if abs(flo) < abs(fhi) else hi
    for _ in range(iters):
        mid = (lo + hi) / 2.0
        fm = fn(mid)
        if fm is None:
            return mid
        if (fm > 0) == (flo > 0):
            lo, flo = mid, fm
        else:
            hi = mid
    return (lo + hi) / 2.0


def frame(points, dist, pitch=304.0, fov=70.0):
    """找一个镜头目标，让这些点在可见区里上下、左右都居中。

    返回 (tx, ty, 装得下吗, 横向占比, 纵向占比)。points 空返回 None。
    镜头目标往北挪，点在屏幕上往下走（v 变大）；往东挪，点往左走 —— 都是单调的，
    所以分别二分；横竖会互相影响一点（纵深变了横向缩放就变），交替两轮就稳了。
    """
    pts = [(float(x), float(y)) for x, y in points]
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    tx, ty = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
    want_v = (VIS_TOP + VIS_BOT) / 2.0

    def mid_v(t):
        sp = _span(pts, tx, t, dist, pitch, fov)
        return None if sp is None else (sp[2] + sp[3]) / 2.0 - want_v

    def mid_u(t):
        sp = _span(pts, t, ty, dist, pitch, fov)
        return None if sp is None else (sp[0] + sp[1]) / 2.0 - 0.5

    for _ in range(2):
        ty = _bisect(mid_v, ty - 4000.0, ty + 4000.0)
        tx = _bisect(mid_u, tx - 4000.0, tx + 4000.0)
    sp = _span(pts, tx, ty, dist, pitch, fov)
    if sp is None:
        return tx, ty, False, 1.0, 1.0
    fits = sp[0] >= VIS_L and sp[1] <= VIS_R and sp[2] >= VIS_TOP and sp[3] <= VIS_BOT
    return tx, ty, fits, sp[1] - sp[0], sp[3] - sp[2]


def aim_offset(dist, pitch=304.0, fov=70.0):
    """单个点（跟拍的英雄）落在可见区正中时，镜头目标相对它的偏移 (dx, dy)。"""
    tx, ty, _, _, _ = frame([(0.0, 0.0)], dist, pitch, fov)
    return tx, ty


def visible(points, tx, ty, dist, pitch=304.0, fov=70.0):
    """这些点里有几个落在可见区里（给"有没有人跑出画面"和复盘用）。"""
    n = 0
    for x, y in points:
        p = project(x - tx, y - ty, dist, pitch, fov)
        if p and VIS_L <= p[0] <= VIS_R and VIS_TOP <= p[1] <= VIS_BOT:
            n += 1
    return n
