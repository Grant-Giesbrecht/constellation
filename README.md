<h1 align="center">
<img src="https://github.com/Grant-Giesbrecht/heimdallr/blob/main/docs/images/heimdallr_logo.png?raw=True" width="500">
</h1><br>

Heimdallr is a package for simplifying instrument control. It is designed to build
off of libraries like [pyvisa](https://github.com/pyvisa/pyvisa) and [pyvisa-py](https://github.com/pyvisa/pyvisa-py)
and provide a complete ecosystem for instrument automation. As a brief example of
what this can look like in its simplest form, here's an example script which 
connects to an instrument, resets it, then adjusts and reads some basic settings:

``` Python
from heimdallr.all import *

# Create log object
log = LogPile()

# Create NRX Driver
nrx = RohdeSchwarzNRX("TCPIP0::192.168.0.10::INSTR", log)

# Preset device
nrx.preset()

# Get meas frequency
nrx.set_meas_frequency(1e9)
fmeas = nrx.get_meas_frequency()
```

One of the key components of Heimdallr is a set of instrument drivers, one of which,
the `RohdeSchwarzNRX` class, was seen above. However, Heimdallr is more than just
a collection of driver classes. Some of its key features include:

- **Instrument API standardization:** Drivers inherit from category classes, guaranteeing
that all instruments of the same category (ie. all oscilloscopes) will share a common
API.
- **Networking:** In addition to directly connecting to and interfacing with your
instruments, you can optionally use Heimdallr's networking classes to remotely access
your instruments. This works by connecting multiple clients to a single server program
over an AES-encrypted network. Typically one client would interface with the instrument 
drivers, while the other clients can be used to monitor or adjust experiments remotely.
- **Autmoatic Rich Logging:** Because Heimdallr's core use-case concerns scientific experiments,
robust and thorough logging is crucial. Heimdallr automates this via the [pylogfile](https://pypi.org/project/pylogfile/)
library and records every command sent to the instruments. Logs can be saved in the binary and open-source HDF format, which can be viewed and analyzed usign the `lumberjack` command line tool.
- **Ease of Creating New Drivers:** The instrument category classes automate much of the 
work involved in creating a driver, meaning you only need to focus on finding the right
SCPI commands to create any new drivers you need.
- **(Work in progress) GUIs:** GUI widgets for specific instrument categories make it easy to control 
or monitor your experiments.

# Installation

Heimdallr can be installed via pip using 

```
pip install heimdallr-py
```

# TODO

### Technical detail: Category system and Drivers

- How the categories work, with 0-n
- How they use ABCs to force correct usage
- They inherit RemoteInstrument so without the end user paying attention, they can be used across a network!

Include graphic of cateogry system, including remote access, drivers and GUIs.

### Instrument Control Example

TODO = basic example

## Networking

### Technical Detail: Networking

- Mention TCPIP, AES, passwords, automatically setup database and server.
- Mention PyFrost (WIP)

### Networking Example

TODO = Networking example

### List of included drivers

[Read The Docs](https://heimdallr-py.readthedocs.io/en/latest/)

[PyPI](https://pypi.org/project/heimdallr-py/)
