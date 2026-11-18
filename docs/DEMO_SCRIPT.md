# SHOUT LAN — Demo runbook (two laptops)

About 8 minutes. The Mac is node 1, the Windows laptop is node 2. Everything runs with Wi-Fi and Bluetooth switched off.

## Before anyone is watching (10 minutes)

1. **Place the laptops** 30-50 cm apart, speakers facing each other, on a firm surface. Once it works, do not move them: the sound path between two laptops changes with their position.
2. **Close every other program that plays sound** on both (browser tabs with video or music, Teams, Spotify).
3. **Set the volume and leave it:** Mac 100 %, Windows 70 %. Never change it while a frame is in the air.
4. **Start both nodes:** double-click `start_mac.command` on the Mac and `start_windows.bat` on the Windows laptop. Each opens its dashboard (`http://127.0.0.1:8081` on the Mac, `http://127.0.0.1:8082` on Windows). Click "Open the console".
5. **Check the Windows laptop says the speaker is exclusive.** If the start window prints a warning about SHARED mode, another program is using the sound card: close it and start again.
6. **Send three plain messages each way** (type `HELLO`, send). They should show "Delivered". One resend now and then is normal.
7. **Press Tools -> Clear** on both dashboards so the audience starts with a clean screen.

If plain messages keep failing in this room, the two tone frequencies do not suit it. Run the tone trial on both laptops at the same time (about 7 minutes, see the end of this page), then set the winning pair.

## The demo

| Step | Do | What the audience sees and hears | What to say |
|---|---|---|---|
| 1 | Show Wi-Fi and Bluetooth are off on both laptops | | "No Wi-Fi, no Bluetooth, no cable. The only thing between these laptops is air." |
| 2 | Mac: type `HELLO`, send | The Mac beeps for about 3 s; a parcel crosses the screen; the Windows laptop beeps back for about 2 s; "Delivered" with two ticks | "Each beep is one bit: low is 0, high is 1. The short reply is the receipt. Only when the receipt is heard does it say delivered." |
| 3 | Click the parcel in the strip at the bottom | The frame's layers, its bytes, its checksum | "The text is wrapped in addresses and a checksum, exactly like a frame on Ethernet or Wi-Fi." |
| 4 | Mac: switch **Generated noise** on (Clicks) | Sharp clicks from the Mac's speaker; flashes on the stage | "Now the room is noisy. This is real sound from the speaker, not a simulation." |
| 5 | Mac: send `CAN YOU HEAR ME` | Parcels arrive damaged on the Windows screen: "Seal broken", two different checksum values. The Mac's deadline ring runs out; "2nd try", "3rd try" | "A click flipped a bit. The receiver recalculated the checksum, it did not match, so it threw the frame away and stayed silent. The sender heard no receipt, so it is sending again." |
| 6 | While it is still retrying: Mac: switch **Noise Shield** on | The next try goes out as a shielded parcel and is delivered; "bits repaired" counts up | "Same noise. The shield adds spare bits, so the receiver repairs the damage instead of throwing the frame away. That is forward error correction: Hamming(7,4)." |
| 7 | Send one more message with the shield on | Delivered, usually first try | "It costs airtime: a shielded frame takes about 1.75 times as long." |
| 8 | Noise off, shield off | | Take questions |

Point at the **Windows** screen in step 5: the damaged parcel and the mismatching checksums show on the laptop that receives the frame. The sender only knows that no receipt came.

## Honest answers to likely questions

- **"Does the shield beat any noise?"** No. Hamming(7,4) repairs one flipped bit in every 7. It handles short bursts like clicks. Strong steady noise, or noise that hides the start-of-frame chirp, still beats it, and then the frame is resent as before.
- **"Why did that message need a second try without any noise?"** The room itself is a noisy channel: echo and reflections damage some frames. The checksum catches them and the protocol resends. That is the point of the project.
- **"Why 50 bits a second?"** Laptop speakers and room echo. Each tone must die away before the next bit, which limits the rate.
- **"Is the noise real?"** Yes, the laptop plays it through its speaker. It is labelled "generated" on screen because we switch it on ourselves so the demo is repeatable.

## If something goes wrong

| Problem | Do |
|---|---|
| A message shows "Not delivered" with no noise on | Send it again. If it keeps happening, move the laptops a little closer and check both volumes |
| The Windows start window shows `ERROR:` | Read the line: it names the cause (sound card in use, port in use). Close other audio programs and double-click the start file again |
| The dashboard says the engine is disconnected | The start window was closed. Double-click the start file again and reload the page |
| The room is too loud for anything to work | Backup 1: on the Mac alone, run `.venv/bin/python shout.py --node 1 --pair` (two nodes sharing the Mac's speaker and microphone, still real sound). Backup 2: `--simulated` instead of `--pair` (no sound, labelled SIMULATED everywhere) |

## Tone trial (only if plain messages fail in a new room)

Run at the same time on both laptops, with both nodes already started:

```
Mac:      .venv/bin/python tools/tone_trial.py --port 8081 --role b --minutes 7
Windows:  .venv\Scripts\python tools\tone_trial.py --port 8082 --role a --minutes 7
```

Each prints, per pair of tones, how many frames it decoded. Pick the pair with the most frames decoded on BOTH laptops, then start both nodes with it, for example `shout.py --node 1 --peer 2 --f0 3000 --f1 5000` (and the same `--f0 --f1` on the other laptop). The default 3000 / 5000 Hz was chosen this way on 2026-11-18.
