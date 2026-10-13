@echo off
rem SoundField TEST launcher - synonyms from the new game SFX thesaurus.
rem
rem ASCII ONLY (cmd reads .bat in the OEM codepage - see run_dev.bat).
rem
rem Same as run_basehead.bat, but the thesaurus file is
rem   py\app\data\game_sfx_thesaurus.tsv
rem (BaseHead format, built by tools\game_thesaurus\ - see its README.md).
rem The installed app is not affected.
cd /d "%~dp0"
set "SOUNDFIELD_BASEHEAD_THESAURUS=%~dp0py\app\data\game_sfx_thesaurus.tsv"
call "%~dp0run_basehead.bat"
