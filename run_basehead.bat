@echo off
rem SoundField TEST launcher - synonyms from the BaseHead thesaurus only.
rem
rem ASCII ONLY (cmd reads .bat in the OEM codepage - see run_dev.bat).
rem
rem What this does:
rem   Sets SOUNDFIELD_THESAURUS=basehead and starts the app the same way as
rem   run_dev.bat (release exe + python sources under py\app\).
rem   Database._related_synonyms then takes synonyms from
rem   C:\Program Files\basehead\Thesaurus.tsv instead of the UCS thesaurus,
rem   without the UCS-era limits (4+ letters, 3+ letter synonyms, stopwords).
rem   Everything else in search is unchanged. The installed app is not affected.
rem   Another file: set SOUNDFIELD_BASEHEAD_THESAURUS=<path> before running.
cd /d "%~dp0"

if "%SOUNDFIELD_BASEHEAD_THESAURUS%"=="" set "SOUNDFIELD_BASEHEAD_THESAURUS=C:\Program Files\basehead\Thesaurus.tsv"
if not exist "%SOUNDFIELD_BASEHEAD_THESAURUS%" (
  echo BaseHead thesaurus not found:
  echo   %SOUNDFIELD_BASEHEAD_THESAURUS%
  pause
  exit /b 1
)

set "SOUNDFIELD_THESAURUS=basehead"
call "%~dp0run_dev.bat"
