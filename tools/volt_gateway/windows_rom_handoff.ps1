param([switch]$AttemptHandoff)
# Tiny native WinUSB handoff to avoid USB/IP rebind latency in the old softloader.
# No flash unlock/erase/program/bulk/protection operations. Default is IN-only.
$ErrorActionPreference = 'Stop'
$target = @((& 'C:\Program Files\usbipd-win\usbipd.exe' state | ConvertFrom-Json).Devices |
  Where-Object { $_.InstanceId -eq 'USB\VID_BBAA&PID_DDCC\370022000651363038363036' })
if ($target.Count -ne 1 -or $target[0].BusId -ne '2-2' -or $target[0].ClientIPAddress) {
  throw 'Expected Windows-owned labeled Panda at verified physical port.'
}
$source = @'
using System;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using Microsoft.Win32.SafeHandles;
public static class LegacyPandaHandoff {
  [StructLayout(LayoutKind.Sequential, Pack=1)]
  struct Setup { public byte Type,Request; public ushort Value,Index,Length; }
  [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]
  static extern SafeFileHandle CreateFile(string name,uint access,uint share,IntPtr security,uint creation,uint flags,IntPtr template);
  [DllImport("winusb.dll",SetLastError=true)] static extern bool WinUsb_Initialize(SafeFileHandle file,out IntPtr handle);
  [DllImport("winusb.dll",SetLastError=true)] static extern bool WinUsb_ControlTransfer(IntPtr h,Setup p,byte[] b,uint length,out uint actual,IntPtr overlap);
  [DllImport("winusb.dll")] static extern bool WinUsb_Free(IntPtr h);
  static string Path(string pid) { return @"\\?\usb#vid_bbaa&pid_"+pid+@"#370022000651363038363036#{cce5291c-a69f-4995-a4c2-2ae57a51ade9}"; }
  static byte[] Read(IntPtr h,byte req,int size) {
    byte[] b=new byte[size]; uint n;
    if(!WinUsb_ControlTransfer(h,new Setup{Type=0xc0,Request=req,Length=(ushort)size},b,(uint)size,out n,IntPtr.Zero))
      throw new Exception("Read failed: "+Marshal.GetLastWin32Error());
    Array.Resize(ref b,(int)n); return b;
  }
  static void Reset(IntPtr h,ushort value) {
    uint n;
    bool ok=WinUsb_ControlTransfer(h,new Setup{Type=0x40,Request=0xd1,Value=value},new byte[0],0,out n,IntPtr.Zero);
    Console.WriteLine("D1 value="+value+" returned="+ok+" error="+(ok?0:Marshal.GetLastWin32Error())+"; not retried");
  }
  public static void Run(bool attempt) {
    using(var f=CreateFile(Path("ddcc"),0xc0000000,3,IntPtr.Zero,3,0x40000000,IntPtr.Zero)) {
      IntPtr h;
      if(f.IsInvalid || !WinUsb_Initialize(f,out h)) throw new Exception("Cannot open original application");
      try {
        string version=Encoding.ASCII.GetString(Read(h,0xd6,64)).TrimEnd('\0');
        Console.WriteLine("Application="+version);
        if(version!="v1.7.3-EON-unknown-RELEASE") throw new Exception("Unexpected application version");
        if(!attempt) return;
        Reset(h,1);
      } finally { WinUsb_Free(h); }
    }
    var timer=Stopwatch.StartNew(); int opens=0;
    while(timer.ElapsedMilliseconds<5000) {
      using(var f=CreateFile(Path("ddee"),0xc0000000,3,IntPtr.Zero,3,0x40000000,IntPtr.Zero)) {
        IntPtr h;
        if(!f.IsInvalid && WinUsb_Initialize(f,out h)) {
          opens++;
          try {
            byte[] echo=Read(h,0xb0,12);
            if(echo.Length!=12 || echo[2]!=0xb0 || echo[3]!=0x4f || echo[4]!=0xde || echo[5]!=0xad || echo[6]!=0xd0 || echo[7]!=0x0d)
              throw new Exception("Legacy softloader echo mismatch; no further command");
            Console.WriteLine("Verified legacy softloader at "+timer.ElapsedMilliseconds+" ms");
            Reset(h,0);
            Console.WriteLine("ROM request sent; ROM enumeration still requires verification");
            return;
          } finally { WinUsb_Free(h); }
        }
      }
      Thread.Sleep(5);
    }
    throw new Exception("Softloader unavailable after 5 seconds; successful opens="+opens+"; no ROM request sent");
  }
}
'@
Add-Type -TypeDefinition $source
[LegacyPandaHandoff]::Run($AttemptHandoff.IsPresent)
