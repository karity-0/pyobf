"""Workspace geometry independent of the color palette."""

DESIGNS = ('studio', 'focus', 'orbit')


def design_stylesheet(design, theme):
    if design == 'focus':
        return f'''
        QFrame#card, QFrame#sidebar, QFrame#console {{ border-radius: 5px; }}
        QFrame#toolbar {{ background: {theme.surface}; border: 1px solid {theme.border}; border-radius: 12px; }}
        QToolButton, QToolButton#primary {{ border-radius: 5px; padding: 0px; }}
        QLabel#section {{ color: {theme.accent}; }}
        '''
    if design == 'orbit':
        return f'''
        QFrame#card, QFrame#sidebar, QFrame#console {{ border-radius: 28px; }}
        QFrame#toolbar {{ background: {theme.surface}; border: 1px solid {theme.border}; border-radius: 36px; }}
        QToolButton {{ border-radius: 22px; padding: 0px; }}
        QToolButton#primary {{ border-radius: 28px; padding: 0px; }}
        QLineEdit, QComboBox {{ border-radius: 17px; padding: 8px 14px; }}
        QTreeView::item, QListWidget::item {{ border-radius: 14px; }}
        QLabel#brand {{ font-size: 18px; letter-spacing: 0px; }}
        '''
    return ''
