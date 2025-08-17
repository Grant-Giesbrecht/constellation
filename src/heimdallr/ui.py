from heimdallr.base import *
from heimdallr.networking.net_client import *
import sys
from PyQt6 import QtCore, QtGui
from PyQt6.QtWidgets import QMainWindow, QGridLayout, QPushButton, QSlider, QGroupBox, QWidget, QTabWidget
from PyQt6.QtGui import QAction

class HeimdallrWindow(QMainWindow):
	
	def __init__(self, log:plf.LogPile, add_menu:bool=True):
		super().__init__()
		self.log = log
		
		grid = QGridLayout()
		
		if add_menu:
			self.add_basic_menu_bar()

	def add_basic_menu_bar(self):
		
		self.bar = self.menuBar()
		
		#----------------- File Menu ----------------
		
		self.file_menu = self.bar.addMenu("File")
		
		# self.save_graph_act = QAction("Save Graph", self)
		# self.save_graph_act.setShortcut("Ctrl+Shift+G")
		# self.file_menu.addAction(self.save_graph_act)
		
		self.close_window_act = QAction("Close Window", self)
		self.close_window_act.setShortcut("Ctrl+W")
		self.close_window_act.triggered.connect(self._basic_menu_close)
		self.file_menu.addAction(self.close_window_act)
		
		self.view_log_act = QAction("View Log", self)
		self.view_log_act.setShortcut("Shift+L")
		self.view_log_act.triggered.connect(self._basic_menu_view_log)
		self.file_menu.addAction(self.view_log_act)
		
		#----------------- Edit Menu ----------------
		
		self.edit_menu = self.bar.addMenu("Edit")
		
		# self.save_graph_act = QAction("Save Graph", self)
		# self.save_graph_act.setShortcut("Ctrl+Shift+G")
		# self.file_menu.addAction(self.save_graph_act)
		
		self.refresh_act = QAction("Refresh", self)
		self.refresh_act.setShortcut("Ctrl+R")
		self.refresh_act.triggered.connect(self._basic_menu_refresh)
		self.edit_menu.addAction(self.refresh_act)
	
	def _basic_menu_close(self):
		self.close()
		sys.exit(0)
	
	def _basic_menu_view_log(self):
		self.log.error(f"Log viewing not implemented.")
		pass
	
	def _basic_menu_refresh(self):
		pass

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
	
	@abstractmethod
	def state_to_ui(self):
		pass