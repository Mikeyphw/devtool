@echo off
setlocal
set VERSION=9.7.1
set SHA256=acd53f1edaf02f1a8ff99879f8a34b302661a057d9b063ae9e35b552f804d20a
set BASE=%USERPROFILE%\.gradle\devtool-bootstrap
set DIST=%BASE%\gradle-%VERSION%
set ZIP=%BASE%\gradle-%VERSION%-bin.zip
if not exist "%DIST%\bin\gradle.bat" (
  if not exist "%BASE%" mkdir "%BASE%"
  if not exist "%ZIP%" powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://services.gradle.org/distributions/gradle-%VERSION%-bin.zip' -OutFile '%ZIP%'"
  powershell -NoProfile -Command "$actual=(Get-FileHash -Algorithm SHA256 '%ZIP%').Hash.ToLowerInvariant(); if ($actual -ne '%SHA256%') { Write-Error ('Gradle %VERSION% checksum mismatch: expected %SHA256%, got ' + $actual); Remove-Item -Force '%ZIP%' -ErrorAction SilentlyContinue; exit 2 }"
  if errorlevel 1 exit /b %errorlevel%
  if exist "%DIST%" rmdir /s /q "%DIST%"
  powershell -NoProfile -Command "Expand-Archive -Force '%ZIP%' '%BASE%'"
  if errorlevel 1 exit /b %errorlevel%
)
call "%DIST%\bin\gradle.bat" %*
set EXIT_CODE=%ERRORLEVEL%
endlocal & exit /b %EXIT_CODE%
