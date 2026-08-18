' Create Desktop Shortcut.vbs
' Double-click this ONCE to create a "Chimera" shortcut on your Desktop
' that launches Run Chimera.bat and shows the Chimera icon (chimera.ico).
' You only need to run this once — after that, use the Desktop shortcut
' itself to open Chimera.

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

projectFolder = fso.GetParentFolderName(WScript.ScriptFullName)
batPath = projectFolder & "\Run Chimera.bat"
iconPath = projectFolder & "\frontend\assets\chimera.ico"
desktopPath = shell.SpecialFolders("Desktop")
shortcutPath = desktopPath & "\Chimera.lnk"

If Not fso.FileExists(batPath) Then
    MsgBox "Could not find Run Chimera.bat next to this script." & vbCrLf & _
           "Make sure this .vbs file stays in the same folder as Run Chimera.bat.", vbCritical, "Chimera"
    WScript.Quit
End If

Set shortcut = shell.CreateShortcut(shortcutPath)
shortcut.TargetPath = batPath
shortcut.WorkingDirectory = projectFolder
shortcut.WindowStyle = 1

If fso.FileExists(iconPath) Then
    shortcut.IconLocation = iconPath
Else
    MsgBox "chimera.ico not found — the shortcut will use a default icon instead.", vbExclamation, "Chimera"
End If

shortcut.Description = "Launch Chimera"
shortcut.Save

MsgBox "Desktop shortcut created: Chimera" & vbCrLf & vbCrLf & _
       "You can now close this and use the Desktop shortcut from now on.", vbInformation, "Chimera"
