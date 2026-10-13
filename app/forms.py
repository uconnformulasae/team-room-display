"""Form definitions (Flask-WTF). Every form also carries the CSRF token."""
from flask_wtf import FlaskForm
from wtforms import DateField, DecimalField, IntegerField, PasswordField, StringField
from wtforms.validators import DataRequired, InputRequired, Length, NumberRange, Optional


def strip(value):
    return value.strip() if isinstance(value, str) else value


class LoginForm(FlaskForm):
    password = PasswordField('Team password', validators=[DataRequired()])


class SettingsForm(FlaskForm):
    # Two countdowns, e.g. one for the gas car's competition and one for the EV car's.
    competition_name = StringField('First competition', filters=[strip], validators=[Length(max=60)])
    competition_date = DateField('First competition date', validators=[Optional()])
    competition2_name = StringField('Second competition', filters=[strip], validators=[Length(max=60)])
    competition2_date = DateField('Second competition date', validators=[Optional()])
    banner_text = StringField('Banner text', filters=[strip], validators=[Length(max=140)])
    photo_interval_s = IntegerField('Seconds per photo', validators=[
        InputRequired('Enter a number of seconds.'), NumberRange(5, 600, 'Pick between 5 and 600 seconds.')])
    # 1.5 is where the layout still fits with a banner and a full list at 1080p (SPEC §11).
    font_scale = DecimalField('Text size', places=2, validators=[
        InputRequired('Enter a text size.'), NumberRange(0.5, 1.5, 'Pick a size between 0.5 and 1.5.')])

    def values(self):
        """The settings as the strings stored in the settings table."""
        return {
            'competition_name': self.competition_name.data or '',
            'competition_date': self.competition_date.data.isoformat() if self.competition_date.data else '',
            'competition2_name': self.competition2_name.data or '',
            'competition2_date': self.competition2_date.data.isoformat() if self.competition2_date.data else '',
            'banner_text': self.banner_text.data or '',
            'photo_interval_s': str(self.photo_interval_s.data),
            'font_scale': str(float(self.font_scale.data)),
        }
