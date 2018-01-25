"""A setuptools based setup module.
See:
https://packaging.python.org/en/latest/distributing.html
https://github.com/pypa/sampleproject
"""

# Always prefer setuptools over distutils
from setuptools import setup, find_packages
# To use a consistent encoding
from codecs import open
from os import path

here = path.abspath(path.dirname(__file__))

# Get the long description from the README file
with open(path.join(here, 'README.md'), encoding='utf-8') as f:
    long_description = f.read()

# Arguments marked as "Required" below must be included for upload to PyPI.
# Fields marked as "Optional" may be commented out.

setup(
    name='cumulus-message-adpater',  # Required

    # Versions should comply with PEP 440:
    # https://www.python.org/dev/peps/pep-0440/
    version='1.0.0',  # Required

    # This is a one-line description or tagline of what your project does. This
    # corresponds to the "Summary" metadata field:
    # https://packaging.python.org/specifications/core-metadata/#summary
    description='A python implementation of cumulus message adapter',  # Required
    long_description=long_description,  # Optional
    url='https://github.com/cumulus-nasa/cumulus-message-adpater',  # Optional
    author='Cumulus Authors',  # Optional
    author_email='info@developmentseed.org',  # Optional
    classifiers=[  # Optional
        # Indicate who your project is intended for
        'Intended Audience :: Developers',
        'Topic :: Software Development :: Build Tools',
        # Specify the Python versions you support here. In particular, ensure
        # that you indicate whether you support Python 2, Python 3 or both.
        'Programming Language :: Python :: 2',
        'Programming Language :: Python :: 2.7',
    ],
    keywords='nasa cumulus message adapter',  # Optional
    packages=find_packages(exclude=['.circleci', 'contrib', 'docs', 'tests']),  # Required
    install_requires=['jsonpath-ng', 'boto3'],  # Optional
)
