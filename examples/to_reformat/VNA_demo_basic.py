import pyvisa as pv

# Initialize VISA resource manager
rm = pv.ResourceManager()

# Replace 'GPIB0::1::INSTR' with the actual VISA address of your instrument
instrument_address = "TCPIP0::10.10.71.10::INSTR"

try:
	# Open a connection to the instrument
	inst = rm.open_resource(instrument_address)

	# Send SCPI commands
	inst.write("SYST:PRES")
	inst.write("SENS:SWE:MODE HOLD")
	inst.write("CALC:PAR:DEL:ALL")
	
	# inst.write("CALC:CUST:DEF 'MySMC', 'Scalar Mixer/Converter', 'SC21'")
	# inst.write("DISP:WIND:TRACE:FEED 'MySMC'")
	# inst.write("CALC:PAR:SEL 'MySMC'")
	
	# inst.write("SENS:SWE:POIN 11")
	# inst.write("SENS:MIX:INP:FREQ:START 200e6")
	# inst.write("SENS:MIX:INP:FREQ:STOP 700e6")
	# inst.write("SENS:MIX:LO:FREQ:MODE Swept")
	# inst.write("SENS:MIX:OUTPUT:FREQ:FIX 3.4e9")
	# inst.write("SENS:MIX:OUTP:FREQ:SID HIGH")
	# inst.write("SENS:MIX:INP:POW -17")
	# inst.write("SENS:MIX:LO:POW 10")
	# inst.write("SENS:MIX:CALC LO_1")
	# inst.write("CALC:CUST:DEF 'MyS11', 'Scalar Mixer/Converter', 'S11'")
	# inst.write("DISP:WIND:TRACE2:FEED 'MyS11'")
	# inst.write("CALC:PAR:SEL 'MyS11'")
	# inst.write("CALC:CUST:DEF 'MyIPwr', 'Scalar Mixer/Converter', 'IPwr'")
	# inst.write("DISP:WIND:TRACE3:FEED 'MyIPwr'")
	# inst.write("CALC:PAR:SEL 'MyIPwr'")
	# inst.write("CALC:CUST:DEF 'MyOPwr', 'Scalar Mixer/Converter', 'OPwr'")
	# inst.write("CALC:PAR:SEL 'MyOPwr'")
	# inst.write("DISP:WIND:TRACE4:FEED 'MyOPwr'")
	# inst.write("SENS:SWE:GRO:COUN 1")
	# inst.write("SENS:SWE:MODE GROUPS")
	# inst.query("*OPC?")
	
	# inst.write("CALC:PAR:SEL 'MySMC'")	

	# # Read response (if needed)
	# response = inst.query("CALC:DATA? SDATA")
	# print("Instrument response:", response)

	# Close the connection
	inst.close()

except pv.VisaIOError as e:
	print(f"Error communicating with the instrument: {e}")

finally:
	# Close the resource manager
	rm.close()
