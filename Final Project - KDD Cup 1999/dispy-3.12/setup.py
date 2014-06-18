from distutils.core import setup

setup(name='dispy',
      version='3.11',
      description='Python framework for distributed and parallel computing.',
      long_description=open('README').read(),
      url='http://dispy.sourceforge.net',
      author='Giridhar Pemmasani',
      author_email='pgiri@yahoo.com',
      py_modules=['asyncoro', 'dispy'],
      scripts=['dispy.py', 'dispynode.py', 'dispynetrelay.py', 'dispyscheduler.py'],
      license='The MIT License: http://www.opensource.org/licenses/mit-license.php',
      platforms='any',
      classifiers=[
          'Intended Audience :: Developers',
          'Intended Audience :: Science/Research',
          'License :: OSI Approved :: MIT License',
          'Operating System :: OS Independent',
          'Programming Language :: Python :: 2',
          'Programming Language :: Python :: 2.7',
          'Programming Language :: Python :: 3',
          'Programming Language :: Python :: 3.1',
          'Topic :: Scientific/Engineering',
          'Topic :: Software Development', ]
      )
