param([switch]$RamProbe)
# Fixed local USB-only recovery command. No erase, program or option writes.
$ErrorActionPreference = 'Stop'
$devices = (& 'C:\Program Files\usbipd-win\usbipd.exe' state | ConvertFrom-Json).Devices
$target = @($devices | Where-Object { $_.BusId -eq '2-2' })
$allowed = @('USB\VID_BBAA&PID_DDCC\370022000651363038363036', 'USB\VID_0483&PID_DF11\365236793036')
if ($target.Count -ne 1 -or $target[0].InstanceId -notin $allowed -or $target[0].IsForced) {
  throw 'Exact labeled Panda/physical USB port required.'
}
$source = @'
using System;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using Microsoft.Win32.SafeHandles;
public static class GatewayRecovery {
  [StructLayout(LayoutKind.Sequential, Pack=1)]
  struct Setup { public byte Type,Request; public ushort Value,Index,Length; }
  [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]
  static extern SafeFileHandle CreateFile(string name,uint access,uint share,IntPtr security,uint creation,uint flags,IntPtr template);
  [DllImport("winusb.dll",SetLastError=true)] static extern bool WinUsb_Initialize(SafeFileHandle file,out IntPtr handle);
  [DllImport("winusb.dll",SetLastError=true)] static extern bool WinUsb_ControlTransfer(IntPtr h,Setup p,byte[] b,uint length,out uint actual,IntPtr overlap);
  [DllImport("winusb.dll")] static extern bool WinUsb_Free(IntPtr h);
  public static void Run(bool ram) {
    string path=@"\\?\usb#vid_bbaa&pid_ddcc#370022000651363038363036#{cce5291c-a69f-4995-a4c2-2ae57a51ade9}";
    string expected=ram ? "voltgw-RAM-RECOVERY-v1" : "voltgw-recovery-v1";
    var timer=Stopwatch.StartNew(); int opens=0,reads=0,lastError=0; string lastVersion="";
    while(timer.ElapsedMilliseconds<90000) {
      using(var f=CreateFile(path,0xc0000000,3,IntPtr.Zero,3,0x40000000,IntPtr.Zero)) {
        IntPtr h;
        if(!f.IsInvalid && WinUsb_Initialize(f,out h)) {
          opens++;
          try {
            byte[] b=new byte[64]; uint n;
            bool read=WinUsb_ControlTransfer(h,new Setup{Type=0xc0,Request=0xd6,Length=64},b,64,out n,IntPtr.Zero);
            if(read && n<=64) {
              reads++; string version=Encoding.ASCII.GetString(b,0,(int)n);
              if(version!=lastVersion) { Console.WriteLine("Observed USB version="+version); lastVersion=version; }
            } else lastError=Marshal.GetLastWin32Error();
            if(read && n<=64 && Encoding.ASCII.GetString(b,0,(int)n)==expected) {
              bool sent=WinUsb_ControlTransfer(h,new Setup{Type=0x40,Request=0xb5,Value=0x4757,Index=0x5243},new byte[0],0,out n,IntPtr.Zero);
              Console.WriteLine("Verified recovery personality; fixed B5 request sent once; returned="+sent+". Verify ROM enumeration separately.");
              return;
            }
          } finally { WinUsb_Free(h); }
        } else lastError=Marshal.GetLastWin32Error();
      }
      Thread.Sleep(20);
    }
    throw new Exception("Recovery window not observed; no reset request sent. Opens="+opens+", reads="+reads+", last WinUSB error="+lastError);
  }
}
'@
Add-Type -TypeDefinition $source
[GatewayRecovery]::Run($RamProbe.IsPresent)
