# -*- coding: utf-8 -*-
"""war3-brain：AMAI 式 Python 决策层（第一版）。

结构照 AMAI（SMUnlimited/AMAI ea95cfd，笔记 worldmodel/reports/amai_combat_notes.md）：
  gamedata   兵种表（units.json）和种族常量
  amai       一种强度、一个进攻性旋钮、威胁、目标好不好（和 war3amai.ps1 同一套公式，测试钉住同一组数）
  world      全局世界状态：读内存看全图（没有迷雾），敌我军团聚类、城镇威胁、对方资源和科技
  jobs       任务调度：每个任务一件事，按游戏秒各自的频率跑
  build      声明式建造：「要几个 X、优先级多少」，按优先级走、钱不够就停（AMAI OneBuildLoopAM）
  tables/    表驱动：每个种族每本科技的建造序列、骚扰小队、人格参数
执行层沿用 brains/xwar3/reflex 的毫秒层模块（mod_combat 保命施法集火、mod_scavenge 物品）。
"""
import os
import sys

_r = os.path.dirname(os.path.abspath(__file__))
while not os.path.isfile(os.path.join(_r, "openwar3.root")):
    _r = os.path.dirname(_r) if os.path.dirname(_r) != _r else sys.exit("找不到 openwar3.root")
sys.path.insert(0, os.path.join(_r, "sdk", "python"))
import w3paths  # noqa: E402  OpenWar3 唯一路径中心
w3paths.setup()
