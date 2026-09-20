' Removes the Tally Connector's auto-start entry. Does not stop it if it's
' currently running -- run stop_connector.vbs for that.

Set objShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
startupFolder = objShell.SpecialFolders("Startup")
shortcutPath = startupFolder & "\TallyConnector.lnk"

If fso.FileExists(shortcutPath) Then
    fso.DeleteFile(shortcutPath)
    MsgBox "Auto-start removed. The connector will no longer start automatically at login.", 64, "Removed"
Else
    MsgBox "Auto-start wasn't set up, nothing to remove.", 48, "Nothing to do"
End If
