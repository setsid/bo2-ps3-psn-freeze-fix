# BO2 PS3 PSN freeze fix

> ### There is a tool that does all of this for you
>
> If you would rather not work through the steps below, **PS3 Tools** does the whole thing over your network. Point it at your console and it finds the game, checks which of the three files still need the fix, backs the originals up to your Desktop, applies the patch, and reads the files back off the console to confirm it worked.
>
> You need a PS3 running custom firmware with webMAN MOD on the same network as your PC, and nothing else. No files to copy off the console, and no scetool command lines to get right.
>
> **[Download PS3 Tools](https://github.com/setsid/ps3-tools/releases/latest)**
>
> The rest of this page is the manual route, and the explanation of what the fix actually changes. It is still the place to look if you want to do it yourself, or to understand what the tool is doing on your behalf.

---

![platform](https://img.shields.io/badge/platform-PS3-003791)
![tested](https://img.shields.io/badge/tested-BLES01717%20TU%201.19-brightgreen)
![cfw](https://img.shields.io/badge/CFW-Evilnat%204.93%20CEX-lightgrey)

Black Ops 2 on PS3 freezes the console whenever a PSN session becomes active:
at launch while signed in, on a mode switch, or when signing in from inside
multiplayer. The console stays up, FTP and webMAN keep responding, but the
game never comes back.

This is not a server problem and it is not an account ban. It is a bug in the
game binary, shipped in 2012, which only became reachable in 2019. Two
instructions cause it, one in each of the two executables. This repo patches
both.

Tested on BLES01717 title update 1.19, Evilnat 4.93 CEX Cobra 8.5, PS3 Slim
CECH-2503B. The patcher locates the fault by instruction pattern rather than a
fixed offset, so it should work on other regions and updates, but I have only
verified BLES01717.

There are two ways to apply it. A tool that does the whole thing in one go,
which is the easier route and the one most people should take, and the manual
scetool sequence it wraps. Both are below. The analysis is at the bottom.

---

## Files

Three of them, all in `/dev_hdd0/game/BLES01717/USRDIR/`. Back them up before
you touch anything.

| file | what it is | needs klicensee |
| --- | --- | --- |
| `EBOOT.BIN` | campaign and zombies | no |
| `t6_ps3f.self` | campaign and zombies as well | yes |
| `t6mp_ps3f.self` | multiplayer | yes |

`EBOOT.BIN` and `t6_ps3f.self` decrypt to the same ELF, they are the same
binary signed twice under different names. Which one the console actually loads
I have not pinned down, and I suspect it varies, so do both. Earlier versions of
this readme listed only two files and left `t6_ps3f.self` out. bjocampos tested
that version on his own console and found multiplayer fixed while campaign and
zombies still froze, which is how the third file came to light.

## The tool

Download `bo2-psn-fix.exe` from the releases page. Nothing to install, nothing
to configure, and scetool is inside it so there is nothing to go and find.

It reads the signing parameters out of your own three files, decrypts them,
applies the patch, re-signs with the values it read rather than the ones in
this readme, and checks the result before it writes anything.

Point it at the game folder holding all three files, which on the console is
`/dev_hdd0/game/<TITLE>/USRDIR/`. It checks the three are present, fills in an
output folder beside it, and enables the button once everything is in place.
Press it and watch the progress bar. Then carry on at Deploy below.

As soon as the folder is picked it reads the three headers and shows what it
found: the title ID, what each file is, and its size. That is the point to
check you have the right game and update before anything happens. If the three
files turn out to be from different titles it says so and refuses to run,
because mixing files from two consoles gives you a set that fails on the
console for no obvious reason.

Settings holds a log file next to the output, opening the output folder when
finished, and verification after signing, all on by default, plus a scetool
override for anyone who wants a different build or keyset. If your three files
are scattered rather than in one folder, Advanced lets you choose each one
separately.

It will not write into the folder the originals came from, so it defaults to a
`patched` folder beside it. Everything is built in a temporary folder and only
moved into place once all three have passed their checks, so a run that fails
leaves you with nothing rather than with something that half works.

The checks are the parts that are easy to get wrong by hand:

- all three files have to carry the same ContentID, or they are not from the
  same install
- `EBOOT.BIN` and `t6_ps3f.self` have to decrypt to the same ELF, and the patch
  is applied once and used for both
- each rebuilt file is decrypted again and compared byte for byte against the
  patched ELF that went into it
- the ContentID and the CID_FN hash on the rebuilt file have to match the
  original, because CID_FN is tied to the filename and getting it wrong gives
  you a file that is perfectly valid and will not load

It stops at the first failure and prints what scetool actually said rather than
a summary of it.

When it finishes it tells you where the files are and what to do with them.
Keep your originals: they are the only way back, and they cannot be rebuilt
from the patched copies.

One limitation. scetool has to run with its own folder as the working
directory, and Windows will not accept a UNC path for that. Run the exe from a
local drive rather than from a network share or a `\\wsl$\...` path. It checks
for this at startup and says so rather than failing partway through a job.

## Doing it by hand

The tool is a wrapper around the following. Nothing here is different, it is
just manual, and step 2 is the only part that is not scetool.

You will need scetool with its `data` folder of keys, which is bundled with
several PS3 toolsets. I would avoid TrueAncestor: its output drops the NPDRM
header and will not boot on a console that checks. Python 3 for step 2.

### 1. Decrypt

scetool decrypts all three directly. `EBOOT.BIN` is free, the two selfs need
the klicensee:

```
scetool -d EBOOT.BIN spzm.elf
scetool -l 8C10AC1473DF38ADD7A4F2EE8C838DAB -d t6mp_ps3f.self mp.elf
```

You only need two ELFs out of the three files, since `EBOOT.BIN` and
`t6_ps3f.self` give you the same thing.

### 2. Patch

```
python3 patch-bo2.py spzm.elf spzm.patched.elf
python3 patch-bo2.py mp.elf mp.patched.elf
```

Each run prints the address it found and the instruction it replaced. If it
cannot find the pattern it stops without writing anything.

Expected output for BLES01717 1.19:

```
"crm %lld %s" at vaddr 0078C384
call at vaddr 004A80F0 (file 004980F0): 4BCCCE4D -> 60000000
```

```
"crm %lld %s" at vaddr 0096AB74
call at vaddr 0050B414 (file 004FB414): 4BC8FA85 -> 60000000
```

### 3. Re-sign

One file at a time, with scetool. This is the `t6_ps3f.self` case; the other
two differ only in `-c`, `-g`, the klicensee, and which ELF goes in.

```
scetool -0 SELF -1 TRUE -s FALSE -2 1C -5 NPDRM \
  -3 1010000001000003 -4 01000002 -A 0001000000000000 -6 0004002000000000 \
  -b FREE -c USPRX -f YOUR_CONTENTID -l 8C10AC1473DF38ADD7A4F2EE8C838DAB \
  -g t6_ps3f.self -e spzm.patched.elf t6_ps3f.patched.self
```

Read `YOUR_CONTENTID` off your own file with `scetool -i t6_ps3f.self` rather
than copying mine. On a 1.19 install it is the update ID, something like
`UP0002-BLUS31011_00-CODBLOPS2PATCH09`, not the disc ID. `-g` has to be the
name the file will have on the console, it feeds a hash of the filename into
the header.

That klicensee is the same across regions. scetool needs it because the game
spawns `t6_ps3f.self` and `t6mp_ps3f.self` with it rather than the free one.

## Deploy

The tool writes all three under their console names, so the folder it produces
maps straight across:

```
curl -T patched/EBOOT.BIN \
  ftp://YOUR_PS3_IP/dev_hdd0/game/BLES01717/USRDIR/EBOOT.BIN

curl -T patched/t6_ps3f.self \
  ftp://YOUR_PS3_IP/dev_hdd0/game/BLES01717/USRDIR/t6_ps3f.self

curl -T patched/t6mp_ps3f.self \
  ftp://YOUR_PS3_IP/dev_hdd0/game/BLES01717/USRDIR/t6mp_ps3f.self
```

Verify what landed:

```
curl -s ftp://YOUR_PS3_IP/dev_hdd0/game/BLES01717/USRDIR/EBOOT.BIN | sha1sum
```

All three files are now fake signed, so syscalls must be enabled or none of
them will boot. Check with `http://YOUR_PS3_IP/syscall8.ps3` and re-create them
from Evilnat's PSN Tools if they are off.

Reboot the console fully before testing.

## Rolling back

Push your backups over the top. No other files are touched, and nothing is
written to flash.

## Reference hashes

BLES01717 title update 1.19.

Stock:

```
6108656  fceadf136dd4fbb6d0cb72f7df4a1eb35f7a33cb  EBOOT.BIN
6108656  457ba9131098a124b26e80b955722198e48dac35  t6_ps3f.self
7254288  0099df2812e45fcc36642df0ac014c4e3d4641c9  t6mp_ps3f.self
```

Decrypted, before patching. `EBOOT.BIN` and `t6_ps3f.self` both give the first
one:

```
11706540  ecb4be9b614ea0b4529da45ef61f35f62521e519  spzm.elf
14215768  46b43843c9b553e589a9abb262bfa609d89f9031  mp.elf
```

These two are not region specific. BLUS31011 and BLES01718 decrypt to the same
two ELFs as BLES01717, checked across three consoles and two regions, so they
are worth comparing against whatever region you are on. The signed files above
and below are not: those differ per region and per set of signing parameters.

Patched, as built here. Your re-signed files will not match these byte for byte
unless your signing parameters are identical, which is fine:

```
6095184  bf32afcbefe96e1424215ffa5036088007f8d10e  EBOOT.BIN
7214528  8af1f859c9fc0a96aae7b2e23abf5dd19b2cdce7  t6mp_ps3f.self
```

---

## What the bug actually is

The game builds a log line using a format template, `crm %lld %s`, which means
"a 64 bit number here, then a string here". The number is supplied correctly.
The string slot is handed the return value of the millisecond timer instead.

Strings are passed as addresses. So the formatting code takes that timer value,
treats it as a memory address, and tries to read text from it. In the dump I
captured the value was 0x602F, 24623 in decimal, roughly 25 seconds of uptime.
Nothing is mapped there. The read faults, lv2 halts the entire process, and you
get a frozen game with a live console.

The call site, in the SP/ZM binary:

```
004a80cc  lis   r4,0x79
004a80d0  ori   r31,r3,0        save destination buffer
004a80d4  addic r30,r4,-0x3c7c  r30 = 0x0078C384  "crm %lld %s"
004a80d8  bl    <ms timer>
004a80dc  ori   r7,r3,0         timer return lands in the %s slot
004a80e0  ori   r3,r31,0        destination
004a80e4  li    r4,0x1b8        buffer size
004a80e8  ori   r5,r30,0        format
004a80ec  ori   r6,r29,0        account id, this one is correct
004a80f0  bl    <snprintf>      faults
004a80f4  lis   r3,0xb3
004a80f8  li    r4,0x1
004a80fc  stb   r4,-0x2b24(r3)  once only latch
```

The branch containing this only runs when a lookup fails to find the account in
a table. Post 2019 accounts appear to reliably produce that miss, which matches
the long standing community reports, but I have only tested one account so
treat the 2019 link as correlation rather than something I have shown here. The
crash does not care why the lookup missed. The bug had been sitting in the
binary untouched since 2012, only reachable when that lookup fails.

The line is pure diagnostics. It writes a string into a buffer, sets a flag,
and returns. No popup, no state change, nothing reads the result. Removing the
call has no effect on anything except that the game stops crashing. That is why
multiplayer works normally afterwards with no error message: there was never a
message, only a broken attempt to log something.

## How I found it

Static analysis was a dead end. I traced the Demonware sign in path in Ghidra,
found the syscall that fetches the account id, and nopped it in both binaries.
Verified the patch live in memory through PS3MAPI. The game froze in exactly the
same way, which ruled out the connect path entirely and cost me a fair amount of
time.

The problem was that I had no way to see where the process was actually stopped.
PS3MAPI has no thread or register access. CCAPI does not either, despite what
the marketing implies: its entire API is getProcessList, getProcessName,
getProcessMemory, setProcessMemory and a few console controls. Neither tool can
even read the stacks, because lv2 tags pages in the 0xD range as
SYS_MEMORY_ACCESS_RIGHT_PPU_THR, owning thread only, and the debugger is not the
owning thread.

What worked was dumping lv2 itself. `dump.ps3?lv2` gives you 8MB containing
around 216 thread structures, each with the full saved register context:

```
+0x08  name[28]
+0x24  thread id (high 32 bits)
+0x70  stack address
+0x78  stack size
+0x98  GPR0-31
+0x198 CR
+0x1A0 XER
+0x1A8 LR
+0x1B0 CTR
+0x1B8 SRR0, the program counter
+0x1C0 MSR, bit 0x4000 set means user mode
```

Scanning for a stack address in 0xD0000000 to 0xDFFFFFFF immediately followed by
a plausible stack size finds every structure reliably.

With the game hung, every thread in the process sat in kernel mode except one,
the main thread, which was in user mode with SRR0 pointing at a `lbz` inside
strlen. On PowerPC a data storage interrupt leaves SRR0 on the faulting
instruction rather than past it, and I could confirm that reading was correct by
comparing against a thread parked in `Sys_Sleep`, whose SRR0 sits after its `sc`
as it should. r3 and r4 both held 0x602F, which is the argument strlen was
handed.

Two dumps twenty seconds apart were byte identical across the whole structure,
while 44 VSH threads changed. The process was not spinning, it was stopped.

From there it was a stack walk. That needed one more piece: full RAM dumps are
physical, and there is no fixed offset mapping them to process addresses. But
pages are 4K, so a page's offset within the physical dump shares its low 12 bits
with its virtual address. That pins each back chain link to exactly one
candidate offset, and you can recover the mapping by finding the physical page
where the back chain and the saved LR both validate. Walking that gave a clean
16 frame stack, which led back through the sign in state machine to the
formatting call.

One thing worth flagging for anyone doing similar work: the lv2 dump and the RAM
dump must come from the same hang. I spent a while reading stack data from an
earlier session against registers from a later one, and it produces results that
look entirely plausible and are completely wrong.

## Notes

The patch removes a diagnostic log line and nothing else. It does not bypass
anything, does not touch anti cheat, and does not change how the game talks to
Demonware. The underlying lookup still fails, exactly as it did before. The only
difference is that failing to log that failure no longer takes the console down.

The old community workaround, signing out, launching, starting a local match,
signing in mid match and then going online, is no longer needed.

## Credits

scetool is naehrwert's. A compiled copy and its keys are bundled inside the exe
so it runs without any setup. This project is not affiliated with it, and
nothing here modifies it.

bjocampos ran an earlier version of this on his own console and reported that
multiplayer came back while campaign and zombies still froze. That is what
turned up the third file: `t6_ps3f.self` was being loaded and was still
unpatched. Without that test this would have shipped fixing two thirds of the
game.
