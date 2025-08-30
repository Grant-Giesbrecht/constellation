.. Constellation documentation master file, created by
   sphinx-quickstart on Sat Jun  8 11:12:33 2024.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.

.. image:: images/constellation_logo.png
    :target: GitHub_
    :width: 500
    :align: center

Constellation is a package for simplifying instrument control. It is designed to build
off of libraries like `pyvisa`_ and `pyvisa-py`_ and provide a complete ecosystem for 
instrument automation. As a brief example of what this can look like in its simplest form, 
here's an example script which connects to an instrument, resets it, then adjusts and reads 
some basic settings:

.. code-block:: python
	:linenos:
	
	from constellation.all import *
	
	# Create log object
	log = plf.LogPile()
	
	# Create NRX Driver
	nrx = RohdeSchwarzNRX("TCPIP0::192.168.0.10::INSTR", log)
	
	# Preset device
	nrx.preset()
	
	# Get meas frequency
	nrx.set_meas_frequency(1e9)
	fmeas = nrx.get_meas_frequency()

One of the key components of Constellation is a set of instrument drivers, one of which,
the :code:`RohdeSchwarzNRX` class, was seen above. However, Constellation is more than just
a collection of driver classes. Some of its key features include:

- **Instrument API standardization:** Drivers inherit from category classes, guaranteeing 
  that all instruments of the same category (ie. all oscilloscopes) will share a common
  API.
- **Networking:** In addition to directly connecting to and interfacing with your
  instruments, you can optionally use Constellation's networking classes to remotely access
  your instruments. This works by connecting multiple clients to a single server program
  over an AES-encrypted network. Typically one client would interface with the instrument 
  drivers, while the other clients can be used to monitor or adjust experiments remotely.
- **Autmoatic Rich Logging:** Because Constellation's core use-case concerns scientific 
  experiments, robust and thorough logging is crucial. Constellation automates this via the `pylogfile`_ library.
  library and records every command sent to the instruments. Logs can be saved in the
  binary and open-source HDF format, which can be viewed and analyzed usign the :code:`lumberjack` command line tool.
- **Ease of Creating New Drivers:** The instrument category classes automate much of the 
  work involved in creating a driver, meaning you only need to focus on finding the right
  SCPI commands to create any new drivers you need.
- **(Work in progress) GUIs:** GUI widgets for specific instrument categories make it easy to control 
  or monitor your experiments.

Installation
============

Constellation can be installed via pip using 

.. code-block::
	
	pip install constellation-py 

Links
=====

This is how you can link to the `GitHub`_ or `PyPI`_.

Documentation
=============

.. toctree::
   :maxdepth: 2
   :caption: Contents:
   
   overview.md
   installation
   modules
   pages/what_why_constellation.md



Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`

.. _pyvisa-py: https://github.com/pyvisa/pyvisa-py
.. _pyvisa: https://github.com/pyvisa/pyvisa
.. _GitHub: https://github.com/Grant-Giesbrecht/Constellation
.. _PyPI: https://pypi.org/project/constellation-py/
.. _what_and_why: https://pypi.org/project/constellation-py/
.. _pylogfile: https://github.com/Grant-Giesbrecht/pylogfile