' Stops a currently-running (hidden) Tally Connector process, without
' affecting any other Python programs you might have running.
' This does NOT undo auto-start -- it'll start again at your next login.
' Run uninstall_autostart.vbs for that.

strComputer = "."
Set objWMIService = GetObject("winmgmts:\\" & strComputer & "\root\cimv2")
Set colProcesses = objWMIService.ExecQuery("Select * from Win32_Process Where Name = 'pythonw.exe' Or Name = 'python.exe'")

found = False
For Each objProcess In colProcesses
    If InStr(objProcess.CommandLine, "connector.py") > 0 Then
        objProcess.Terminate()
        found = True
    End If
Next

If found Then
    MsgBox "Tally Connector stopped.", 64, "Stopped"
Else
    MsgBox "Tally Connector wasn't running.", 48, "Not running"
End If
