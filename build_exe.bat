@echo off
rem Builds one standalone DummySite.exe. Run once on any Windows PC that has Python 3.12+.
rem The result runs on the weak PC with NO Python installed.
chcp 65001 >nul
python -m pip install --upgrade pyinstaller cryptography
python -m unittest tests.test_stage1 tests.test_stage2_certs tests.test_stage3_service tests.test_stage4_cli tests.test_stage5_policy tests.test_stage6_features
if errorlevel 1 (
  echo Tests failed. The EXE was not built.
  exit /b 1
)
python -m PyInstaller --onefile --noconsole --name DummySite DummySite.pyw
echo.
echo Done. Copy dist\DummySite.exe to the weak PC.
