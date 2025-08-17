from heimdallr.base import *
from heimdallr.networking.net_client import *

from PyQt6 import QtCore, QtGui
from PyQt6.QtWidgets import QMainWindow, QGridLayout, QPushButton, QSlider, QGroupBox, QWidget, QTabWidget

class HeimdallrWindow(QMainWindow):
	
	def __init__(self, log:plf.LogPile):
		super().__init__()
		self.log = log
		
		grid = QGridLayout()

class InstrumentWidget(QWidget):
	
	def __init__(self, main_window, driver:Driver, log:plf.LogPile):
		super().__init__(main_window)
		
		
		# Local variables
		self.main_window = main_window
		self.log = log
		self.driver = driver
		
		self.main_layout = QGridLayout()
		
		# # Automatically check if a local driver or remoteinstrument was provided
		# self.is_remote = issubclass(type(self), RemoteInstrument)