from constellation.base import *
from constellation.networking.net_client import *
import sys
from PyQt6 import QtCore, QtGui
from PyQt6.QtWidgets import QMainWindow, QGridLayout, QPushButton, QSlider, QGroupBox, QWidget, QTabWidget, QLabel, QSizePolicy, QSpacerItem
from PyQt6.QtGui import QAction, QPixmap

import matplotlib.pyplot as plt
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas, NavigationToolbar2QT

class StatusPushButton(QWidget):
	
	def __init__(self, button_text, parent):
		super().__init__(parent)
		
		# Local variables
		self.parent = parent
		
		self.main_layout = QGridLayout()
		
		# Create button
		self.main_button = QPushButton(button_text, parent=parent)
		self.main_button.setCheckable(True)
		self.main_button.clicked.connect(lambda: self.set_status(self.main_button.isChecked()))
		
		# Get indicator sprites
		self.indicator_0 = QPixmap("/Users/grantgiesbrecht/Documents/GitHub/constellation/src/constellation/assets/indicator_0.png").scaledToWidth(40)
		self.indicator_1 = QPixmap("/Users/grantgiesbrecht/Documents/GitHub/constellation/src/constellation/assets/indicator_1.png").scaledToWidth(40)
		
		
		# Create indicator
		self.status_indicator = QLabel()
		self.status_indicator.setPixmap(self.indicator_0)
		
		# Create spacer
		self.spacer_h = QSpacerItem(10, 10, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum) 
		
		self.main_layout.addWidget(self.main_button, 0, 0)
		self.main_layout.addItem(self.spacer_h, 0, 1)
		self.main_layout.addWidget(self.status_indicator, 0, 2)
		
		self.setLayout(self.main_layout)
	
	# def _auto_set_status(self):
	# 	self.set_status(self.main_button.isChecked())
	
	def set_status(self, status:bool):
		
		if status:
			self.status_indicator.setPixmap(self.indicator_1)
		else:
			self.status_indicator.setPixmap(self.indicator_0)
