@echo off
rem ======================================================================
rem  OpenWar3: stop everything (same as "start.bat stop").
rem
rem  Stops, in this order: game instances (games, farms, AIs, directors), the gateway,
rem  chat bubbles, website preview, the local LLM model this system uses (LM Studio
rem  itself stays open), and finally the Farsight backend.
rem  Not touched: MCP servers (owned by their LLM client such as Claude), Warcraft III
rem  windows this system did not start.
rem
rem  Keep the local LLM loaded in VRAM:  stop.bat --keep-llm
rem  Start again:                        start.bat
rem ======================================================================
call "%~dp0start.bat" stop %*
