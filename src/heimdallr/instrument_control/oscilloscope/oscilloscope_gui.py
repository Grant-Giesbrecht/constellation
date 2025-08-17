from heimdallr.base import *
from heimdallr.instrument_control.oscilloscope.oscilloscope_ctg import *
from heimdallr.ui import InstrumentWidget

from PyQt6.QtWidgets import QWidget, QGridLayout, QLabel, QTextEdit, QPushButton, QLineEdit
from PyQt6.QtGui import QDoubleValidator

class ChannelWidget(QWidget):
	
	def __init__(self, main_window, driver:Driver, log:plf.LogPile, channel_num:int):
		super().__init__(main_window)
		
		self.main_window = main_window
		self.driver = driver
		self.log = log
		self.channel_num = channel_num
		
		self.main_layout = QGridLayout()
		
		self.chan_label = QLabel(f"Channel {channel_num}")
		
		self.vdiv_label = QLabel("Volts/div:")
		self.vdiv_edit = QLineEdit()
		self.vdiv_edit.setValidator(QDoubleValidator())
		vdiv_val = driver.state[BasicOscilloscopeCtg.DIV_VOLT].get_ch_val(self.channel_num)
		self.vdiv_edit.setText(f"{vdiv_val}")
		self.vdiv_edit.setFixedWidth(80)
		
		self.main_layout.addWidget(self.chan_label, 0, 0, 1, 2)
		self.main_layout.addWidget(self.vdiv_label, 1, 0)
		self.main_layout.addWidget(self.vdiv_edit, 1, 1)
		# self.xmin_edit.editingFinished.connect(self.apply_changes)
		
		self.setLayout(self.main_layout)

class BasicOscilloscopeWidget(InstrumentWidget):
	
	def __init__(self, main_window, driver:Driver, log:plf.LogPile):
		super().__init__(main_window, driver, log)
		
		self.auto_widgets = []
		
		for i in range(self.driver.first_channel, self.driver.first_channel+self.driver.max_channels):
			self.auto_widgets.append(ChannelWidget(self.main_window, self.driver, self.log, i))
			
			self.main_layout.addWidget(self.auto_widgets[-1], 0, i)
		
		self.setLayout(self.main_layout)