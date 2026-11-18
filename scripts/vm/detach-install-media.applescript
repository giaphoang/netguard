tell application "UTM"
    set vm to virtual machine named "NetGuard-Ubuntu-ARM64"
    if status of vm is not stopped then error "Wait until the installed guest powers off."
    set cfg to get configuration of vm
    set diskList to drives of cfg
    set keepDrives to {}
    repeat with diskEntry in diskList
        if not removable of diskEntry then set end of keepDrives to {id:id of diskEntry}
    end repeat
    if (count of keepDrives) is not 1 then error "Expected a single fixed system disk."
    if (count of diskList) > 1 then update configuration of vm with {drives:keepDrives}
    set cfg to get configuration of vm
    return drives of cfg
end tell
