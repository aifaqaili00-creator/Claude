# Shows one Windows notification. Called by notify.py: toast.ps1 -Title "..." -Body "..."
param([string]$Title = 'Product Checker', [string]$Body = '')
try {
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
    $t = [System.Security.SecurityElement]::Escape($Title)
    $b = [System.Security.SecurityElement]::Escape($Body)
    $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
    $xml.LoadXml("<toast><visual><binding template=`"ToastGeneric`"><text>$t</text><text>$b</text></binding></visual></toast>")
    $appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show(
        [Windows.UI.Notifications.ToastNotification]::new($xml))
} catch {
}
exit 0
