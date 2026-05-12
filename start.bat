@echo off
echo Starting Music Video Studio...
echo.

:: Start backend in a new terminal
start "MV Studio - Backend" cmd /k "cd /d %~dp0backend && pip install -r requirements.txt --quiet && python -m uvicorn app.main:app --reload --port 8000"

:: Wait a moment for backend to boot
timeout /t 3 /nobreak > nul

:: Start frontend in a new terminal
start "MV Studio - Frontend" cmd /k "cd /d %~dp0frontend && npm install --silent && npm run dev"

echo.
echo Backend:  http://localhost:8000
echo Frontend: http://localhost:3000
echo API Docs: http://localhost:8000/docs
echo.
echo Both servers are starting in separate windows.
pause
