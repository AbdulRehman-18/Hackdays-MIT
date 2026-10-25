"""Tool groups.

QUICK   one-shot tools for the voice brain
CHAT    what the chat agent can use (research tools return raw material it reads, instead of finished answers)
SCREEN  the screen agent's apps + UI tools (it sees the screen every step)"""
from . import apple, apps, ax, create, files, media, research, screen, senses, system, web

QUICK = [*apps.TOOLS, *web.TOOLS, *files.TOOLS, *system.TOOLS, *apple.TOOLS, *media.TOOLS, research.news,
         *create.TOOLS, *senses.TOOLS]
CHAT = [*apps.TOOLS, web.open_url, web.google, web.youtube, web.weather, *research.TOOLS, files.find_files,
        files.open_file, files.read_file, files.move_to_trash, *system.TOOLS, *apple.TOOLS, *media.TOOLS,
        *create.TOOLS, *senses.TOOLS]
SEARCH = {t.name for t in research.TOOLS}  # switched off when web search is off
SCREEN = [apps.open_app, web.open_url, files.open_file, *ax.TOOLS, *screen.TOOLS]
ALL = {t.name: t for t in [*QUICK, *CHAT, *SCREEN]}
