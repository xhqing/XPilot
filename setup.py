from setuptools import setup, find_packages

setup(
    name='xpilot',
    version='0.2.0',
    description='A CLI proxy toolkit with xray backend',
    packages=find_packages(),
    python_requires='>=3.8',
    install_requires=[
        'click>=8.0',
        'requests>=2.28',
        'requests[socks]>=2.28',
        'pyyaml>=6.0',
    ],
    entry_points={
        'console_scripts': [
            'xpilot=xpilot.cli:cli',
            'dev-proxy=dev.dev_proxy:main',
        ],
    },
)
