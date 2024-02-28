from setuptools import setup

setup(name='cbpi4_GlycolChillerWithDependantTargetTemperature',
      version='0.0.1',
      description='CraftBeerPi Plugin',
      author='Víctor Andueza',
      author_email='vandueza13@gmail.com',
      url='https://github.com/victorandueza/cbpi4_GlycolChillerWithDependantTargetTemperature',
      include_package_data=True,
      package_data={
        # If any package contains *.txt or *.rst files, include them:
      '': ['*.txt', '*.rst', '*.yaml'],
      'cbpi4_FermenterHysteresisWithChillerDiff': ['*','*.txt', '*.rst', '*.yaml']},
      packages=['cbpi4_FermenterHysteresisWithChillerDiff'],
     )
