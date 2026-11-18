on run argv
    set installer to POSIX file (item 1 of argv)
    set seedDisk to POSIX file (item 2 of argv)
    set projectDir to POSIX file (item 3 of argv)
    set cores to (item 4 of argv) as integer
    set ram to (item 5 of argv) as integer
    tell application "UTM"
        set vmName to "NetGuard-Ubuntu-ARM64"
        if exists (virtual machine named vmName) then error "NetGuard VM already registered."
        set vm to make new virtual machine with properties {backend:apple, configuration:{name:vmName, memory:ram, cpu cores:cores, displays:{{dynamic resolution:false}}, network interfaces:{{mode:shared}}, drives:{{removable:true, source:installer}, {guest size:32768}, {removable:true, source:seedDisk}}}}
        update registry of vm with {projectDir}
        return id of vm
    end tell
end run
