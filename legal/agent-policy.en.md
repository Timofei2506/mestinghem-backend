# Device Control Policy

This Policy governs the use of the **Mestinghem AI** agent — a software component that, with your explicit consent, is installed on your computer and allows the Platform to perform a limited set of actions on it. It complements the Terms of Service and the Privacy Policy.

- Operator: **Mestinghem AI UAB**, Vilnius, Lithuania
- Version: 1.0

## 1. What the agent is

The agent is a program installed on a device you own or lawfully manage. It performs only the actions allowed by the selected profile and confirmed by you. The agent does not act on its own and does not initiate actions without a command and the corresponding permissions.

## 2. Explicit consent and pairing

- Connecting a device requires your explicit consent to this Policy.
- Pairing is done with a single-use code shown once and valid for a limited time, plus a unique device key.
- You may revoke a device at any time; after revocation the agent immediately loses access.

## 3. Permission profiles

Available actions are determined by the profile you choose when connecting:
- **Observer** — read only: system information, window list, screen view, reading files in trusted folders.
- **Assistant** — additionally launching applications and reading the clipboard.
- **Operator** — additionally writing files in trusted folders, changing settings, closing applications, writing to the clipboard.
- **Full** — the whole action set, but write operations still require confirmation.

The profile can be changed in device settings. Narrowing a profile takes effect immediately.

## 4. Trusted areas

File operations are allowed only within the trusted folders you specify during pairing. Access to paths outside the trusted list is blocked.

## 5. Confirmation of risky actions

- Each action is classified by risk level.
- Elevated- and critical-risk actions never run automatically and require your explicit confirmation.
- Requests initiated by artificial intelligence are not self-approved: anything that is not low-risk is queued for your decision.
- Operations capable of causing irreparable harm (deleting system data, formatting, writing executables and scripts to protected areas) are forbidden outright and cannot be confirmed.

## 6. Command authenticity

Commands sent to the agent are signed and verified for authenticity to prevent tampering. The agent executes only correctly signed commands that match its profile.

## 7. Logging and transparency

All requested, approved, denied and executed actions are recorded in the device log available to you in the interface. You can see what was done, who requested it and with what result.

## 8. Data the agent processes

The agent transmits to the Platform only the information necessary to perform the requested actions, within the selected profile. We do not covertly record keystrokes, do not collect content outside trusted areas, and do not use device data for purposes unrelated to executing your commands.

## 9. Prohibited use

You may not install the agent on someone else's device without the owner's knowledge and consent, or use it for covert surveillance, credential harvesting or any action that breaches the Acceptable Use Policy or the law.

## 10. Ceasing use

You may revoke a device, change the profile or remove the agent at any time. After revocation access stops immediately, and the device log is retained per the Privacy Policy.

---

Questions: **privacy@mestinghem.ai**. Mestinghem AI UAB · Vilnius, Lithuania
