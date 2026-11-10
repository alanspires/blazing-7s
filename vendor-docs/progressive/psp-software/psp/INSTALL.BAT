@ECHO OFF

CLS
ECHO.
ECHO.
ECHO.

ECHO.
ECHO.
ECHO     Copying files ....
ECHO.

IF EXIST C:\psp301\NUL GOTO SKIP
        md c:\psp301

:SKIP
copy *.hlp c:\psp301
copy *.fil c:\psp301
copy *.exe c:\psp301
copy *.mk2 c:\psp301
copy *.cn2 c:\psp301
c:
cls
echo.
echo.
echo.
echo.
echo           Install successfully!
echo.
echo.
cd \psp301

