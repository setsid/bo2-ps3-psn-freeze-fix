# BO2 PS3 PSN freeze fix

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

## Using the patch

You need the BO2 Eboot-Self Builder to decrypt and re-sign. There is no public
mirror, it lives on NGU behind a free login:

`https://www.nextgenupdate.com/forums/black-ops-2-modding-tools/783979-releasecexdexbo2-eboot-self-builder-2.html`

Back up your originals first. Both live in
`/dev_hdd0/game/BLES01717/USRDIR/`:

- `EBOOT.BIN`, the campaign and zombies launcher
- `t6mp_ps3f.self`, multiplayer

### 1. Decrypt

Run each file through the builder. It leaves the decrypted ELF at
`Tools/Temp/tmp.elf`. Copy that out and rename it before doing the second one,
or you will overwrite it.

scetool will not decrypt `t6mp_ps3f.self` directly, it fails with "Could not
decrypt header". Use the builder.

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

Back through the builder, one file at a time.

For the campaign and zombies binary, tick output `EBOOT.BIN`. For multiplayer,
tick output `t6mp_ps3f.self`. Type CEX, and your region. Leave the SPRX loader
off unless you actually want it.

Output lands in `Output/` with the region in parentheses in the filename, for
example `(BLES01717)EBOOT.BIN`.

The builder names the output after the box you ticked, not after what you fed
it. If you feed it the multiplayer ELF and tick EBOOT.BIN, you will get a file
called EBOOT.BIN containing multiplayer, and pushing that to the console
replaces your zombies launcher. Check the file sizes.

### 4. Deploy

```
curl -T "Output/(BLES01717)EBOOT.BIN" \
  ftp://YOUR_PS3_IP/dev_hdd0/game/BLES01717/USRDIR/EBOOT.BIN

curl -T "Output/(BLES01717)t6mp_ps3f.self" \
  ftp://YOUR_PS3_IP/dev_hdd0/game/BLES01717/USRDIR/t6mp_ps3f.self
```

Verify what landed:

```
curl -s ftp://YOUR_PS3_IP/dev_hdd0/game/BLES01717/USRDIR/EBOOT.BIN | sha1sum
```

Both files are now fake signed, so syscalls must be enabled or neither will
boot. Check with `http://YOUR_PS3_IP/syscall8.ps3` and re-create them from
Evilnat's PSN Tools if they are off.

Reboot the console fully before testing.

## Reference hashes

BLES01717 title update 1.19.

Stock:

```
6108656  fceadf136dd4fbb6d0cb72f7df4a1eb35f7a33cb  EBOOT.BIN
7254288  0099df2812e45fcc36642df0ac014c4e3d4641c9  t6mp_ps3f.self
```

Patched, as built here. Your re-signed files will not match these byte for byte
unless your builder settings are identical, which is fine:

```
6095184  bf32afcbefe96e1424215ffa5036088007f8d10e  EBOOT.BIN
7214528  8af1f859c9fc0a96aae7b2e23abf5dd19b2cdce7  t6mp_ps3f.self
```

## Rolling back

Push your backups over the top. No other files are touched, and nothing is
written to flash.

## Notes

The patch removes a diagnostic log line and nothing else. It does not bypass
anything, does not touch anti cheat, and does not change how the game talks to
Demonware. The underlying lookup still fails, exactly as it did before. The only
difference is that failing to log that failure no longer takes the console down.

The old community workaround, signing out, launching, starting a local match,
signing in mid match and then going online, is no longer needed.
