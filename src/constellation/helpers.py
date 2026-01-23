from constellation.base import *

def check_online(dvr:Driver, name:str, log:plf.LogPile):
	if dvr.online:
		log.info(f"{name} >ONLINE<.")
	else:
		log.info(f"Failed to connect to {name}.")
		exit()
