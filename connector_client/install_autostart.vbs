' Sets up the Tally Connector to start automatically, invisibly, whenever
' you log into Windows -- and starts it right now too, so you don't have to
' log out and back in to test it.
'
' Run this once. Double-click it (it needs no special permissions).

Set objShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
startupFolder = objShell.SpecialFolders("Startup")
connectorPath = scriptDir & "\connector.py"

If Not fso.FileExists(connectorPath) Then
    MsgBox "Could not find connector.py next to this file." & vbCrLf & _
           "Make sure install_autostart.vbs is in the same folder as connector.py.", 16, "Setup failed"
    WScript.Quit
End If

On Error Resume Next
Set shortcut = objShell.CreateShortcut(startupFolder & "\TallyConnector.lnk")
shortcut.TargetPath = "pythonw.exe"
shortcut.Arguments = """" & connectorPath & """"
shortcut.WorkingDirectory = scriptDir
shortcut.WindowStyle = 7
shortcut.Description = "Tally Connector - keeps the web app connected to Tally"
shortcut.Save
On Error Goto 0

If Err.Number <> 0 Then
    MsgBox "Setup failed: " & Err.Description & vbCrLf & vbCrLf & _
           "This usually means Python isn't installed, or wasn't added to PATH during install. " & _
           "Reinstall Python from python.org and make sure to check 'Add Python to PATH'.", 16, "Setup failed"
    WScript.Quit
End If

' Start it now too, so there's no need to log out/in to see it working.
objShell.Run "pythonw.exe """ & connectorPath & """", 0, False

MsgBox "Done. The Tally Connector will now start automatically every time you log in, with no window shown." & vbCrLf & vbCrLf & _
       "It's also running right now. Check connector.log in this folder in a few seconds to confirm it connected." & vbCrLf & vbCrLf & _
       "To stop it, run stop_connector.vbs in this same folder. To remove auto-start, run uninstall_autostart.vbs.", _
       64, "Setup complete"
