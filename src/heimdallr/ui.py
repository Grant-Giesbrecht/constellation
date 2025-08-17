from heimdallr.base import *
from heimdallr.networking.net_client import *
import sys
from PyQt6 import QtCore, QtGui
from PyQt6.QtWidgets import QMainWindow, QGridLayout, QPushButton, QSlider, QGroupBox, QWidget, QTabWidget
from PyQt6.QtGui import QAction

import matplotlib.pyplot as plt
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas, NavigationToolbar2QT

class HeimdallrWindow(QMainWindow):
	
	def __init__(self, log:plf.LogPile, add_menu:bool=True):
		super().__init__()
		self.log = log
		
		self.instrument_widgets = []
		
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
		
		self.refresh_act = QAction("Refresh UI from State", self)
		self.refresh_act.setShortcut("Ctrl+R")
		self.refresh_act.triggered.connect(self._basic_menu_state_to_ui)
		self.edit_menu.addAction(self.refresh_act)
	
	def _basic_menu_close(self):
		self.close()
		sys.exit(0)
	
	def _basic_menu_view_log(self):
		self.log.error(f"Log viewing not implemented.")
		pass
	
	def _basic_menu_state_to_ui(self):
		
		for iw in self.instrument_widgets:
			iw.state_to_ui()

class PlotWidget(QWidget):
	
	def __init__(self, main_window, log:plf.LogPile, cust_render_func:callable=None, **kwargs): #, xlabel:str="", ylabel:str="", title:str="", ):
		super().__init__(main_window)
		
		self.main_window = main_window
		self.log = log
		self.custom_render_func = cust_render_func
		
		# Create figure in matplotlib
		self.fig1 = plt.figure()
		self.gs = self.fig1.add_gridspec(1, 1)
		self.ax1a = self.fig1.add_subplot(self.gs[0, 0])
		
		# Create Qt Figure Canvas
		self.fig_canvas = FigureCanvas(self.fig1)
		self.fig_toolbar = NavigationToolbar2QT(self.fig_canvas, self)
		
		self.grid = QGridLayout()
		self.grid.addWidget(self.fig_toolbar, 0, 0)
		self.grid.addWidget(self.fig_canvas, 1, 0)
		
		self.setLayout(self.grid)
		
		self._render_widget()
	
	def _render_widget(self):
		
		# Call custom renderer if provided
		if self.custom_render_func is not None:
			self.custom_render_func(self)
		
		self.fig1.tight_layout()
		self.fig1.canvas.draw_idle()
		
		self.is_current = True

class InstrumentWidget(QWidget):
	
	def __init__(self, main_window, driver:Driver, log:plf.LogPile):
		super().__init__(main_window)
		
		# Local variables
		self.main_window = main_window
		self.log = log
		self.driver = driver
		
		self.main_layout = QGridLayout()
		
		self.main_window.instrument_widgets.append(self)
		
		# # Automatically check if a local driver or remoteinstrument was provided
		# self.is_remote = issubclass(type(self), RemoteInstrument)
	
	@abstractmethod
	def state_to_ui(self):
		pass