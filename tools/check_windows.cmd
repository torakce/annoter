@echo off
rem Windows check of the native display engine (PLAN.md, steps 4-5).
rem Double-click: runs the engine tests, then the before / after display
rem bench on the synthetic plan (generated the first time). Drop a real
rem PDF on this file to measure it too. Results: bench\check_windows.txt.
rem The bench opens Annoter windows: leave the mouse alone until the end.
setlocal
set "ROOT=%~dp0.."
set "PY=%ROOT%\.venv\Scripts\python.exe"
set "OUT=%ROOT%\bench\check_windows.txt"
if not exist "%ROOT%\bench" mkdir "%ROOT%\bench"
echo Annoter native engine check, %DATE% %TIME%> "%OUT%"
echo Running the engine tests...
"%PY%" -m pytest "%ROOT%\tests\test_engine_native.py" "%ROOT%\tests\test_tiled_page_item.py" "%ROOT%\tests\test_engine_annots.py" -q -p no:cacheprovider >> "%OUT%" 2>&1
if exist "%ROOT%\bench\plans\plans_15xA0.pdf" goto measure
echo Generating the synthetic plans...
"%PY%" "%ROOT%\tools\genplans.py" --annotated >> "%OUT%" 2>&1
:measure
echo Measuring the synthetic plan: PyMuPDF, then the engine...
"%PY%" "%ROOT%\tools\bench_display.py" "%ROOT%\bench\plans\plans_15xA0.pdf" --renderer both >> "%OUT%" 2>&1
if "%~1"=="" goto done
echo Measuring the dropped PDF...
"%PY%" "%ROOT%\tools\bench_display.py" "%~f1" --renderer both >> "%OUT%" 2>&1
:done
echo Done: results in bench\check_windows.txt
pause
