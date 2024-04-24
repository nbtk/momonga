import setuptools


with open('README.md', 'r') as f:
    long_description = f.read()


setuptools.setup(
    name='momonga',
    version='0.0.2',
    description='Python Route B Library: A Comunicator for Low-voltage Smart Electric Energy Meters',
    long_description=long_description,
    long_description_content_type='text/markdown',
    url='https://github.com/nbtk/momonga',
    author='nbtk',
    license='MIT',
    classifiers=[
        'Development Status :: 2 - Pre-Alpha',
        'Programming Language :: Python :: 3',
        'License :: OSI Approved :: MIT License',
    ],
    packages=setuptools.find_packages(),
    install_requires=[
        'pyserial~=3.5',
    ],
    python_requires='>=3.10',
)
