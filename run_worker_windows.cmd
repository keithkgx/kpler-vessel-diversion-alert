@echo off
setlocal
cd /d "%~dp0" || exit /b 1

if not exist ".venv\Scripts\python.exe" (
    echo [%date% %time%] Missing .venv\Scripts\python.exe>>"worker.log"
    exit /b 1
)

rem Scheduled runs must write results even if an interactive shell used DRY_RUN=1.
set "DRY_RUN=0"
echo [%date% %time%] Scheduled run starting>>"worker.log"
".venv\Scripts\python.exe" "shipment_update_flagging_workflow.py" >>"worker.log" 2>&1
set "worker_exit=%errorlevel%"
echo [%date% %time%] Scheduled run exit code: %worker_exit%>>"worker.log"
exit /b %worker_exit%
